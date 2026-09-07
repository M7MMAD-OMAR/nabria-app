using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Security.Principal;
using System.Text.Json;
using System.Windows;

namespace Nabria.Desktop;

public partial class App : Application
{
    private Mutex? instance;
    private bool ownsMutex;
    private readonly string pipeName = "NabriaDesktop-" + WindowsIdentity.GetCurrent().User!.Value;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        Native.SetCurrentProcessExplicitAppUserModelID("com.sbarah.Nabria");
        string command = e.Args.FirstOrDefault() ?? "open";
        if (command == "--self-test") { await Check(); return; }
        instance = new Mutex(true, "Local\\" + pipeName, out ownsMutex);
        if (!ownsMutex)
        {
            try
            {
                using var pipe = new NamedPipeClientStream(".", pipeName, PipeDirection.Out, PipeOptions.Asynchronous);
                await pipe.ConnectAsync(5000);
                using var writer = new StreamWriter(pipe) { AutoFlush = true };
                await writer.WriteLineAsync(command);
            }
            catch (Exception error) { MessageBox.Show(error.Message, "Nabria"); }
            Shutdown(); return;
        }
        if (command == "quit") { Shutdown(); return; }
        try
        {
            var window = new MainWindow();
            MainWindow = window;
            if (command == "daemon" || command == "--background") window.WindowState = WindowState.Minimized;
            window.Show();
            _ = Serve(window);
        }
        catch (Exception error)
        {
            MessageBox.Show(Strings.T("desktop.start_failed") + "\n\n" + error.Message, "Nabria", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
        }
    }

    private async Task Serve(MainWindow window)
    {
        while (!window.IsClosed)
        {
            try
            {
                using var pipe = new NamedPipeServerStream(pipeName, PipeDirection.In, 1, PipeTransmissionMode.Byte,
                    PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync();
                using var reader = new StreamReader(pipe);
                string? command = await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(3));
                await Dispatcher.InvokeAsync(() => window.ExternalCommand(command ?? "open"));
            }
            catch (Exception) { if (window.IsClosed) return; }
        }
    }

    private async Task Check()
    {
        try
        {
            using (var process = Process.Start(Backend.Python("-m", "nabria", "--self-test"))!)
            using (var job = new Native.Job())
            {
                job.Assign(process);
                var output = process.StandardOutput.ReadToEndAsync();
                var errors = process.StandardError.ReadToEndAsync();
                await process.WaitForExitAsync();
                await Task.WhenAll(output, errors);
                if (process.ExitCode != 0) { Shutdown(process.ExitCode); return; }
            }
            if (Native.GetConsoleWindow() != IntPtr.Zero) throw new InvalidOperationException("Desktop attached a console");
            var window = new MainWindow();
            MainWindow = window;
            window.Show();
            await window.Ready.WaitAsync(TimeSpan.FromSeconds(45));
            window.CheckPages();
            string? report = Environment.GetEnvironmentVariable("NABRIA_TEST_REPORT");
            if (!string.IsNullOrEmpty(report))
            {
                var data = JsonSerializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(report))!;
                data["native_desktop_pages"] = "passed in English and Arabic";
                data["console_window"] = "absent";
                File.WriteAllText(report, JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true }));
            }
            await window.StopBackend();
            Shutdown(0);
        }
        catch (Exception error)
        {
            string? report = Environment.GetEnvironmentVariable("NABRIA_TEST_REPORT");
            if (!string.IsNullOrEmpty(report)) File.WriteAllText(report, JsonSerializer.Serialize(new { status = "failed", error = error.ToString() }));
            Shutdown(1);
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        if (ownsMutex) instance?.ReleaseMutex();
        instance?.Dispose();
        base.OnExit(e);
    }
}
