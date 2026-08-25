using System.Diagnostics;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

Console.InputEncoding = Encoding.UTF8;
Console.OutputEncoding = Encoding.UTF8;
Console.Title = "Local Model Console";

var app = new ModelConsole();
await app.RunAsync();

sealed class ModelConsole
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromMinutes(30) };
    private readonly string? _distro = Environment.GetEnvironmentVariable("LOCAL_MODEL_CONTROL_DISTRO");
    private string? _wslRoot;

    public async Task RunAsync()
    {
        while (true)
        {
            var status = await ControllerAsync("status");
            var models = ParseModels(status);
            var selected = Select(models, status);
            if (selected is null) return;
            if (selected == "__stop")
            {
                await ControllerAsync("stop", "--confirm");
                ContinuePrompt("All registered models are stopped.");
                continue;
            }
            if (selected == "__refresh") continue;
            if (selected == "__discover")
            {
                await DiscoverAndInstallAsync();
                continue;
            }

            var model = models.First(item => item.Id == selected);
            if (!model.Available)
            {
                ContinuePrompt($"Unavailable. Missing: {string.Join(", ", model.Missing)}");
                continue;
            }
            if (!model.Healthy)
            {
                Console.Clear();
                Console.WriteLine($"Switch to {model.Name}? This stops the current registered model. [y/N]");
                if (Console.ReadKey(true).Key != ConsoleKey.Y) continue;
                var switched = await ControllerAsync("switch", model.Id, "--confirm");
                if (switched.TryGetProperty("error", out var error))
                {
                    ContinuePrompt(error.GetString() ?? "Switch failed.");
                    continue;
                }
            }
            await ChatAsync(model);
        }
    }

    private string? Select(List<ModelInfo> models, JsonElement status)
    {
        var entries = models.Select(item => new MenuEntry(item.Id, item.Name, item.Description, item.Available, item.Healthy)).ToList();
        entries.Add(new("__stop", "Stop all models", "Release registered model processes and leave GPUs free.", true, false));
        entries.Add(new("__discover", "Discover / install a model", "Search public licensed GGUFs, check hardware fit, then install and smoke-test.", true, false));
        entries.Add(new("__refresh", "Refresh", "Reload status.", true, false));
        entries.Add(new("__exit", "Exit", "Close the console without changing the active model.", true, false));
        var index = Math.Max(0, entries.FindIndex(entry => entry.Running));

        while (true)
        {
            Console.Clear();
            Header();
            var active = status.TryGetProperty("active", out var activeNode) && activeNode.ValueKind != JsonValueKind.Null
                ? activeNode.GetProperty("name").GetString()
                : "None";
            Console.WriteLine($"Active: {active}\n");
            Console.WriteLine("Use ↑/↓ and Enter to select; Esc exits.\n");
            for (var i = 0; i < entries.Count; i++)
            {
                var entry = entries[i];
                Console.ForegroundColor = i == index ? ConsoleColor.Cyan : entry.Available ? ConsoleColor.Gray : ConsoleColor.DarkGray;
                var marker = i == index ? "▶" : " ";
                var state = entry.Running ? " [RUNNING]" : entry.Available ? "" : " [MISSING]";
                Console.WriteLine($"{marker} {entry.Name}{state}");
                Console.ForegroundColor = ConsoleColor.DarkGray;
                Console.WriteLine($"    {entry.Description}");
            }
            Console.ResetColor();
            var key = Console.ReadKey(true).Key;
            if (key == ConsoleKey.UpArrow) index = (index - 1 + entries.Count) % entries.Count;
            else if (key == ConsoleKey.DownArrow) index = (index + 1) % entries.Count;
            else if (key == ConsoleKey.Escape) return null;
            else if (key == ConsoleKey.Enter) return entries[index].Id == "__exit" ? null : entries[index].Id;
        }
    }

    private async Task DiscoverAndInstallAsync()
    {
        Console.Clear();
        Header();
        Console.Write("Search Hugging Face (blank = newest): ");
        var search = Console.ReadLine()?.Trim();
        Console.WriteLine("Inspecting hardware and public GGUF artifacts…");
        var args = new List<string> { "discover", "--limit", "10" };
        if (!string.IsNullOrWhiteSpace(search)) { args.Add("--search"); args.Add(search); }
        var result = await ScriptAsync("discovery.py", args.ToArray());
        if (ShowError(result)) return;
        var candidates = JsonSerializer.Deserialize<List<Candidate>>(result.GetProperty("candidates").GetRawText(), JsonOptions.Options) ?? new();
        if (candidates.Count == 0) { ContinuePrompt("No matching installable GGUF was found."); return; }

        var index = 0;
        while (true)
        {
            Console.Clear();
            Header();
            Console.WriteLine("Capacity estimates are conservative; actual speed and quality are validated after download.\n");
            for (var i = 0; i < candidates.Count; i++)
            {
                var item = candidates[i];
                Console.ForegroundColor = i == index ? ConsoleColor.Cyan : item.Fit.Tier == "not_recommended" ? ConsoleColor.DarkGray : ConsoleColor.Gray;
                Console.WriteLine($"{(i == index ? "▶" : " ")} {item.RepoId}");
                Console.ForegroundColor = ConsoleColor.DarkGray;
                Console.WriteLine($"    {item.Filename}  {item.SizeGib:F2} GiB  {item.Fit.Tier}  license:{item.License}");
            }
            Console.ResetColor();
            var key = Console.ReadKey(true).Key;
            if (key == ConsoleKey.Escape) return;
            if (key == ConsoleKey.UpArrow) index = (index - 1 + candidates.Count) % candidates.Count;
            else if (key == ConsoleKey.DownArrow) index = (index + 1) % candidates.Count;
            else if (key == ConsoleKey.Enter) break;
        }

        var selected = candidates[index];
        var plan = await ScriptAsync("discovery.py", "plan", selected.RepoId, selected.Filename);
        if (ShowError(plan)) return;
        Console.Clear();
        Header();
        Console.WriteLine($"Repository:  {selected.RepoId}");
        Console.WriteLine($"Artifact:    {selected.Filename}");
        Console.WriteLine($"Download:    {selected.SizeGib:F2} GiB");
        Console.WriteLine($"License:     {selected.License}");
        Console.WriteLine($"Fit:         {selected.Fit.Tier} — {selected.Fit.Reason}");
        Console.WriteLine($"Destination: {plan.GetProperty("destination").GetString()}");
        if (!plan.GetProperty("enough_disk").GetBoolean()) { ContinuePrompt("Not enough disk space."); return; }
        if (selected.Fit.Tier == "not_recommended") { ContinuePrompt("Installation blocked because this artifact does not fit the conservative capacity budget."); return; }
        var suggestedId = MakeId(selected.RepoId.Split('/').Last());
        Console.Write($"\nLocal model ID [{suggestedId}]: ");
        var entered = Console.ReadLine()?.Trim();
        var modelId = string.IsNullOrWhiteSpace(entered) ? suggestedId : entered;
        Console.WriteLine("This downloads the artifact, registers it, stops the active model, starts the new one, and runs a chat smoke test. Continue? [y/N]");
        if (Console.ReadKey(true).Key != ConsoleKey.Y) return;
        var installed = await ScriptAsync("discovery.py", "install", selected.RepoId, selected.Filename, modelId, "--confirm");
        ContinuePrompt(installed.TryGetProperty("error", out var error) ? error.GetString() ?? "Install failed." : "Installed, registered, and smoke-tested successfully.");
    }

    private static string MakeId(string value)
    {
        var cleaned = new string(value.ToLowerInvariant().Select(character => char.IsLetterOrDigit(character) ? character : '-').ToArray()).Trim('-');
        while (cleaned.Contains("--")) cleaned = cleaned.Replace("--", "-");
        return cleaned.Length < 2 ? "local-model" : cleaned[..Math.Min(63, cleaned.Length)].TrimEnd('-');
    }

    private static bool ShowError(JsonElement value)
    {
        if (!value.TryGetProperty("error", out var error)) return false;
        ContinuePrompt(error.GetString() ?? "Operation failed.");
        return true;
    }

    private async Task ChatAsync(ModelInfo model)
    {
        var messages = new List<ChatMessage>();
        Console.Clear();
        Header();
        Console.ForegroundColor = ConsoleColor.Green;
        Console.WriteLine($"Connected: {model.Name} — http://127.0.0.1:{model.Port}");
        Console.ResetColor();
        Console.WriteLine("Commands: /switch  /clear  /stop  /exit\n");

        while (true)
        {
            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.Write("You › ");
            Console.ResetColor();
            var input = Console.ReadLine();
            if (input is null || input.Trim() == "/exit") return;
            if (input.Trim() == "/switch") return;
            if (input.Trim() == "/clear")
            {
                messages.Clear();
                Console.WriteLine("Conversation cleared.\n");
                continue;
            }
            if (input.Trim() == "/stop")
            {
                await ControllerAsync("stop", "--confirm");
                Console.WriteLine("Model stopped.\n");
                return;
            }
            if (string.IsNullOrWhiteSpace(input)) continue;
            messages.Add(new("user", input));

            Console.ForegroundColor = ConsoleColor.Yellow;
            Console.Write($"{model.Name} › ");
            Console.ResetColor();
            try
            {
                var answer = await StreamChatAsync(model, messages);
                messages.Add(new("assistant", answer));
            }
            catch (Exception error)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine($"\nError: {error.Message}");
                Console.ResetColor();
            }
            Console.WriteLine("\n");
        }
    }

    private async Task<string> StreamChatAsync(ModelInfo model, List<ChatMessage> messages)
    {
        var payload = new
        {
            model = model.Alias,
            messages,
            max_tokens = 2048,
            temperature = 0.2,
            stream = true
        };
        using var request = new HttpRequestMessage(HttpMethod.Post, $"http://127.0.0.1:{model.Port}/v1/chat/completions")
        {
            Content = JsonContent.Create(payload)
        };
        using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync();
        using var reader = new StreamReader(stream);
        var answer = new StringBuilder();
        while (await reader.ReadLineAsync() is { } line)
        {
            if (!line.StartsWith("data: ")) continue;
            var data = line[6..];
            if (data == "[DONE]") break;
            using var document = JsonDocument.Parse(data);
            var choices = document.RootElement.GetProperty("choices");
            if (choices.GetArrayLength() == 0) continue;
            var delta = choices[0].GetProperty("delta");
            if (!delta.TryGetProperty("content", out var content) || content.ValueKind != JsonValueKind.String) continue;
            var token = content.GetString() ?? "";
            Console.Write(token);
            answer.Append(token);
        }
        return answer.ToString();
    }

    private async Task<JsonElement> ControllerAsync(params string[] arguments)
        => await ScriptAsync("controller.py", arguments);

    private async Task<JsonElement> ScriptAsync(string script, params string[] arguments)
    {
        _wslRoot ??= await ResolveWslRootAsync();
        var start = new ProcessStartInfo("wsl.exe")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        if (!string.IsNullOrWhiteSpace(_distro))
        {
            start.ArgumentList.Add("-d");
            start.ArgumentList.Add(_distro);
        }
        start.ArgumentList.Add("--");
        start.ArgumentList.Add("python3");
        start.ArgumentList.Add($"{_wslRoot}/{script}");
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        using var process = Process.Start(start) ?? throw new InvalidOperationException("Could not start wsl.exe");
        process.ErrorDataReceived += (_, eventArgs) =>
        {
            if (!string.IsNullOrWhiteSpace(eventArgs.Data)) Console.WriteLine(eventArgs.Data);
        };
        process.BeginErrorReadLine();
        var output = await process.StandardOutput.ReadToEndAsync();
        await process.WaitForExitAsync();
        using var document = JsonDocument.Parse(string.IsNullOrWhiteSpace(output) ? "{\"error\":\"Empty controller response\"}" : output);
        return document.RootElement.Clone();
    }

    private async Task<string> ResolveWslRootAsync()
    {
        var configured = Environment.GetEnvironmentVariable("LOCAL_MODEL_CONTROL_WSL_ROOT");
        if (!string.IsNullOrWhiteSpace(configured)) return configured.TrimEnd('/');
        var windowsRoot = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        var start = new ProcessStartInfo("wsl.exe")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        if (!string.IsNullOrWhiteSpace(_distro)) { start.ArgumentList.Add("-d"); start.ArgumentList.Add(_distro); }
        start.ArgumentList.Add("--cd");
        start.ArgumentList.Add(windowsRoot);
        start.ArgumentList.Add("--");
        start.ArgumentList.Add("pwd");
        using var process = Process.Start(start) ?? throw new InvalidOperationException("Could not start WSL. Install WSL or set LOCAL_MODEL_CONTROL_WSL_ROOT.");
        var output = (await process.StandardOutput.ReadToEndAsync()).Trim();
        var error = (await process.StandardError.ReadToEndAsync()).Trim();
        await process.WaitForExitAsync();
        if (process.ExitCode != 0 || string.IsNullOrWhiteSpace(output)) throw new InvalidOperationException($"Could not map the app folder into WSL: {error}");
        return output.TrimEnd('/');
    }

    private static List<ModelInfo> ParseModels(JsonElement status)
    {
        var value = JsonSerializer.Deserialize<List<ModelInfo>>(status.GetProperty("models").GetRawText(), JsonOptions.Options);
        return value ?? new();
    }

    private static void Header()
    {
        Console.ForegroundColor = ConsoleColor.Magenta;
        Console.WriteLine("╔══════════════════════════════════════╗");
        Console.WriteLine("║       LOCAL MODEL CONTROL            ║");
        Console.WriteLine("╚══════════════════════════════════════╝");
        Console.ResetColor();
    }

    private static void ContinuePrompt(string message)
    {
        Console.WriteLine(message);
        Console.WriteLine("Press any key to continue.");
        Console.ReadKey(true);
    }
}

sealed record MenuEntry(string Id, string Name, string Description, bool Available, bool Running);
sealed record ChatMessage(string role, string content);
sealed record ModelInfo(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("port")] int Port,
    [property: JsonPropertyName("alias")] string Alias,
    [property: JsonPropertyName("available")] bool Available,
    [property: JsonPropertyName("missing")] List<string> Missing,
    [property: JsonPropertyName("running")] bool Running,
    [property: JsonPropertyName("healthy")] bool Healthy);
sealed record FitInfo(
    [property: JsonPropertyName("tier")] string Tier,
    [property: JsonPropertyName("reason")] string Reason);
sealed record Candidate(
    [property: JsonPropertyName("repo_id")] string RepoId,
    [property: JsonPropertyName("filename")] string Filename,
    [property: JsonPropertyName("size_gib")] double SizeGib,
    [property: JsonPropertyName("license")] string License,
    [property: JsonPropertyName("fit")] FitInfo Fit);

static class JsonOptions
{
    public static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true };
}
