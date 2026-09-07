using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace Nabria.Desktop;

internal sealed class Backend : IDisposable
{
    private readonly Process process;
    private readonly Native.Job job = new();
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> pending = new();
    private long nextId;
    private bool disposed;
    public event Action<JsonElement>? Message;
    public event Action<string>? Failed;

    public static ProcessStartInfo Python(params string[] arguments)
    {
        string root = AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        string runtime = Path.Combine(root, "runtime");
        var info = new ProcessStartInfo(Path.Combine(runtime, "bin", "python.exe"))
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8, StandardInputEncoding = new UTF8Encoding(false),
            WorkingDirectory = root
        };
        foreach (string argument in arguments) info.ArgumentList.Add(argument);
        info.Environment["NABRIA_INSTALL_DIR"] = root;
        info.Environment["PYTHONHOME"] = runtime;
        info.Environment["PYTHONPATH"] = Path.Combine(root, "app");
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["GI_TYPELIB_PATH"] = Path.Combine(runtime, "lib", "girepository-1.0");
        info.Environment["XDG_DATA_DIRS"] = Path.Combine(runtime, "share");
        info.Environment["SSL_CERT_FILE"] = Path.Combine(runtime, "cert.pem");
        info.Environment["PATH"] = string.Join(';', Path.Combine(runtime, "bin"), Path.Combine(root, "engine"), Environment.SystemDirectory);
        return info;
    }

    public Backend()
    {
        process = new Process { StartInfo = Python("-m", "nabria", "--desktop-backend") };
        process.Start();
        job.Assign(process);
    }

    public void Listen()
    {
        _ = Task.Run(async () =>
        {
            try
            {
                while (await process.StandardOutput.ReadLineAsync() is { } line)
                {
                    using var document = JsonDocument.Parse(line);
                    var message = document.RootElement.Clone();
                    if (message.GetProperty("event").GetString() == "reply" && message.TryGetProperty("id", out var id)
                        && id.TryGetInt64(out long number) && pending.TryRemove(number, out var source))
                        source.TrySetResult(message);
                    else Message?.Invoke(message);
                }
                if (!disposed) Failed?.Invoke(Strings.T("desktop.backend_stopped"));
            }
            catch (Exception error) { if (!disposed) Failed?.Invoke(error.Message); }
        });
        _ = Task.Run(async () =>
        {
            string folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Nabria", "state");
            Directory.CreateDirectory(folder);
            using var writer = new StreamWriter(Path.Combine(folder, "desktop-backend.log"), append: true);
            while (await process.StandardError.ReadLineAsync() is { } line)
            { await writer.WriteLineAsync(line); await writer.FlushAsync(); }
        });
    }

    public async Task Send(string command, Dictionary<string, object?>? values = null)
    {
        long id = Interlocked.Increment(ref nextId);
        var data = values ?? new();
        data["command"] = command; data["id"] = id;
        var source = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = source;
        try
        {
            await writeLock.WaitAsync();
            try { await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(data)); await process.StandardInput.FlushAsync(); }
            finally { writeLock.Release(); }
            var reply = await source.Task.WaitAsync(TimeSpan.FromSeconds(20));
            if (!reply.GetProperty("ok").GetBoolean()) throw new InvalidOperationException(reply.GetProperty("detail").GetString());
        }
        finally { pending.TryRemove(id, out _); }
    }

    public async Task Stop()
    {
        if (disposed) return;
        disposed = true;
        try
        {
            await process.StandardInput.WriteLineAsync("{\"command\":\"quit\"}");
            await process.StandardInput.FlushAsync();
            await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(4));
        }
        catch (Exception) { /* Closing the job also reaps a stuck backend and engine. */ }
        Dispose();
    }

    public void Dispose() { disposed = true; job.Dispose(); process.Dispose(); }
}
