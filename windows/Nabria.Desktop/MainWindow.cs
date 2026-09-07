using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace Nabria.Desktop;

internal sealed partial class MainWindow : Window
{
    private readonly Backend backend;
    private readonly IndicatorWindow indicator;
    private readonly TaskCompletionSource ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private JsonElement data;
    private string page = "home", state = "idle";
    private bool closing, rebuilding, taskRunning, micTesting;
    private string taskText = "", micText = "";
    private ContentControl content = new();
    private TextBlock? errorLabel;
    private string lastError = "";
    private TextBlock? statusLabel, shortcutLabel, taskLabel, micResult;
    private Button? recordButton, cancelButton;
    private TextBox? lastText;
    private ProgressBar? levelMeter, progress;
    private ListBox? navigation;
    public bool IsClosed { get; private set; }
    public Task Ready => ready.Task;

    public MainWindow()
    {
        ThemeMode = ThemeMode.System;
        var fluent = new ResourceDictionary { Source = new Uri("pack://application:,,,/PresentationFramework.Fluent;component/Themes/Fluent.xaml") };
        Resources.MergedDictionaries.Add(fluent);
        foreach (Type type in new[] { typeof(Button), typeof(ComboBox), typeof(TextBox), typeof(ListBox), typeof(ListBoxItem), typeof(ProgressBar), typeof(CheckBox), typeof(ScrollViewer) })
            Resources[type] = fluent[type];
        Title = "Nabria"; Width = 960; Height = 720; MinWidth = 800; MinHeight = 600;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        FontFamily = new FontFamily("Segoe UI"); FontSize = 15;
        FlowDirection = Strings.IsRtl ? FlowDirection.RightToLeft : FlowDirection.LeftToRight;
        var icon = Path.Combine(AppContext.BaseDirectory, "app", "nabria", "assets", "nabria.png");
        if (File.Exists(icon)) Icon = new BitmapImage(new Uri(icon));
        Content = new TextBlock { Text = Strings.T("desktop.loading"), Margin = new Thickness(40), FontSize = 20 };
        backend = new Backend();
        indicator = new IndicatorWindow(command => Send(command));
        backend.Message += message => Dispatcher.BeginInvoke(() => Receive(message));
        backend.Failed += error => Dispatcher.BeginInvoke(() =>
        {
            if (closing) return;
            ready.TrySetException(new InvalidOperationException(error));
            ShowError(error);
        });
        backend.Listen();
        Closing += OnClosing;
    }

    private static string Text(JsonElement element, string key, string fallback = "") =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(key, out var value) ? value.ToString() : fallback;
    private static bool Flag(JsonElement element, string key) => Text(element, key) == "True" || Text(element, key) == "true";
    private JsonElement Settings => data.GetProperty("settings");
    private string Setting(string key, string fallback = "") => Text(Settings, key, fallback);
    private static double Number(JsonElement element, string key) =>
        element.TryGetProperty(key, out var value) && value.TryGetDouble(out double number) ? number : 0;
    private string T(string key) => Strings.T("desktop." + key);

    private void Receive(JsonElement message)
    {
        if (closing) return;
        switch (Text(message, "event"))
        {
            case "ready":
                bool first = data.ValueKind == JsonValueKind.Undefined;
                data = message;
                historyItems = message.GetProperty("history");
                Strings.Language = Text(message, "language", "en");
                if (Flag(message, "needsSetup")) page = "setup";
                else if (page == "setup") page = "home";
                if (first) ready.TrySetResult();
                BuildShell();
                break;
            case "state":
                state = Text(message, "state", "idle");
                if (statusLabel != null) statusLabel.Text = T("state_" + state);
                if (recordButton != null) recordButton.Content = T(state == "recording" ? "stop" : "start");
                if (cancelButton != null) cancelButton.IsEnabled = state == "recording";
                if (levelMeter != null && !taskRunning) levelMeter.Value = Math.Clamp(Number(message, "level") + 60, 0, 60);
                if (shortcutLabel != null && message.TryGetProperty("shortcuts", out var shortcuts))
                    shortcutLabel.Text = Text(shortcuts, "toggle", T("shortcut_unavailable"));
                indicator.Update(state, Number(message, "level"));
                break;
            case "navigate":
                page = Text(message, "page", "home");
                if (data.ValueKind != JsonValueKind.Undefined) { Navigate(page); Restore(); }
                break;
            case "transcript":
                if (lastText != null) lastText.Text = Text(message, "text");
                historyItems = message.GetProperty("history");
                if (page == "history") Navigate(page);
                break;
            case "progress":
                if (progress != null)
                {
                    progress.IsIndeterminate = false;
                    progress.Value = Number(message, "done") / Math.Max(1, Number(message, "total")) * 100;
                }
                break;
            case "mic_level":
                if (levelMeter != null) levelMeter.Value = Math.Clamp(Number(message, "level") + 60, 0, 60);
                break;
            case "mic_result":
                micText = Flag(message, "cancelled") ? "cancelled" : Flag(message, "heard") ? "mic_good" : "mic_quiet";
                if (micResult != null) micResult.Text = T(micText);
                break;
            case "task_done":
                taskRunning = false; micTesting = false;
                if (progress != null) { progress.IsIndeterminate = false; progress.Value = 0; }
                if (data.ValueKind != JsonValueKind.Undefined) Navigate(page);
                break;
            case "error":
                taskRunning = false;
                lastError = Text(message, "title") + "\n" + Text(message, "detail");
                if (errorLabel != null) { errorLabel.Text = lastError.Trim(); errorLabel.Visibility = Visibility.Visible; }
                break;
        }
    }

    private void BuildShell()
    {
        rebuilding = true;
        FlowDirection = Strings.IsRtl ? FlowDirection.RightToLeft : FlowDirection.LeftToRight;
        var shell = new Grid();
        shell.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(210) });
        shell.ColumnDefinitions.Add(new ColumnDefinition());
        var sidebar = new DockPanel { Margin = new Thickness(12), LastChildFill = true };
        var brand = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(10, 16, 0, 32) };
        brand.Children.Add(new TextBlock { Text = "\uE720", FontFamily = new FontFamily("Segoe MDL2 Assets"), FontSize = 28, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 12, 0) });
        brand.Children.Add(new TextBlock { Text = T("app_name"), FontSize = 26, FontWeight = FontWeights.SemiBold });
        DockPanel.SetDock(brand, Dock.Top); sidebar.Children.Add(brand);
        var attribution = new Button { Padding = new Thickness(10), Margin = new Thickness(0, 16, 0, 4), ToolTip = "sbarah.com" };
        var attributionContent = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
        attributionContent.Children.Add(new TextBlock { Text = Strings.T("brand.credit"), FontSize = 12, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 0, 0, 8) });
        attributionContent.Children.Add(new Border
        {
            CornerRadius = new CornerRadius(6), Padding = new Thickness(8, 4, 8, 4), Background = new SolidColorBrush(Color.FromRgb(31, 42, 31)),
            Child = new Image { Source = new BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "app/nabria/assets/sbarah-logo.png"))), Width = 70, Height = 37, Stretch = Stretch.Uniform, FlowDirection = FlowDirection.LeftToRight }
        });
        attribution.Background = Brushes.Transparent; attribution.BorderThickness = new Thickness(0);
        attribution.Content = attributionContent;
        attribution.SetResourceReference(StyleProperty, "DefaultButtonStyle");
        AutomationProperties.SetName(attribution, Strings.T("brand.credit") + " sbarah.com");
        attribution.Click += (_, _) => Open("https://sbarah.com");
        DockPanel.SetDock(attribution, Dock.Bottom); sidebar.Children.Add(attribution);
        navigation = new ListBox { BorderThickness = new Thickness(0), Background = Brushes.Transparent };
        foreach (var (id, glyph) in new[] { ("home", "\uE80F"), ("history", "\uE81C"), ("settings", "\uE713"), ("help", "\uE897") })
        {
            var row = new Grid();
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            row.ColumnDefinitions.Add(new ColumnDefinition());
            row.Children.Add(new TextBlock { Text = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"), Margin = new Thickness(0, 0, 14, 0), VerticalAlignment = VerticalAlignment.Center });
            var label = new TextBlock { Text = T(id), TextWrapping = TextWrapping.Wrap, FontSize = 15 };
            Grid.SetColumn(label, 1); row.Children.Add(label);
            var item = new ListBoxItem { Content = row, Tag = id, HorizontalContentAlignment = HorizontalAlignment.Stretch, IsSelected = id == page };
            item.SetResourceReference(StyleProperty, "NavigationItem");
            AutomationProperties.SetName(item, T(id));
            AutomationProperties.SetAutomationId(item, "nav-" + id);
            navigation.Items.Add(item);
        }
        navigation.SetResourceReference(StyleProperty, "DefaultListBoxStyle");
        navigation.SelectionChanged += (_, _) => { if (!rebuilding && navigation.SelectedItem is ListBoxItem selected) Navigate((string)selected.Tag); };
        sidebar.Children.Add(navigation);
        var sidebarBorder = new Border { Background = new SolidColorBrush(Color.FromArgb(12, 128, 128, 128)), Child = sidebar };
        shell.Children.Add(sidebarBorder);
        content = new ContentControl { Margin = new Thickness(32, 28, 32, 20), HorizontalContentAlignment = HorizontalAlignment.Stretch };
        var main = new DockPanel();
        errorLabel = new TextBlock { Text = lastError.Trim(), TextWrapping = TextWrapping.Wrap, Margin = new Thickness(32, 16, 32, 0), Visibility = lastError.Length > 0 ? Visibility.Visible : Visibility.Collapsed };
        DockPanel.SetDock(errorLabel, Dock.Top); main.Children.Add(errorLabel); main.Children.Add(content);
        Grid.SetColumn(main, 1); shell.Children.Add(main);
        Content = shell;
        rebuilding = false;
        Navigate(page);
    }

    public void Navigate(string target)
    {
        page = target;
        if (data.ValueKind == JsonValueKind.Undefined) return;
        statusLabel = null; shortcutLabel = null; recordButton = null; cancelButton = null; lastText = null; levelMeter = null; progress = null; taskLabel = null; micResult = null;
        var body = new StackPanel();
        switch (target)
        {
            case "setup": SetupPage(body); break;
            case "history": HistoryPage(body); break;
            case "settings": SettingsPage(body); break;
            case "help": HelpPage(body); break;
            default: HomePage(body); break;
        }
        content.Content = new ScrollViewer { Content = body, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 12, 0) };
    }

    private async Task Send(string command, Dictionary<string, object?>? arguments = null)
    {
        try
        {
            await backend.Send(command, arguments);
            lastError = "";
            if (errorLabel != null) errorLabel.Visibility = Visibility.Collapsed;
        }
        catch (Exception error)
        {
            if (command is "download" or "select_model" or "adopt" or "mic_test")
            { taskRunning = false; Navigate(page); }
            if (!closing) ShowError(error.Message);
        }
    }
    private async Task Set(string key, object value) => await Send("set", new() { ["key"] = key, ["value"] = value });
    private void ShowError(string detail, string? title = null) => MessageBox.Show(this, detail, string.IsNullOrWhiteSpace(title) ? T("attention") : title, MessageBoxButton.OK, MessageBoxImage.Warning);
    private static void Open(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    private void Restore() { if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal; Show(); Activate(); }
    public async void ExternalCommand(string command)
    {
        if (command == "quit") Close();
        else if (command is "toggle" or "cancel" or "stop") await Send(command);
        else { if (command == "settings") Navigate("settings"); Restore(); }
    }
    private async void OnClosing(object? sender, CancelEventArgs e)
    {
        if (closing) return;
        e.Cancel = true;
        if (state != "idle" && MessageBox.Show(this, T("close_busy"), "Nabria", MessageBoxButton.YesNo, MessageBoxImage.Question, MessageBoxResult.No) != MessageBoxResult.Yes) return;
        closing = true;
        await StopBackend();
        Close();
    }
    internal void CheckPages()
    {
        foreach (string language in new[] { "en", "ar" })
        {
            Strings.Language = language;
            BuildShell();
            foreach (string target in new[] { "home", "history", "settings", "help" })
            { Navigate(target); UpdateLayout(); }
            for (setupStep = 0; setupStep < 3; setupStep++) { Navigate("setup"); UpdateLayout(); }
            if (FlowDirection != (language == "ar" ? FlowDirection.RightToLeft : FlowDirection.LeftToRight))
                throw new InvalidOperationException("Desktop direction did not follow its language");
        }
    }
    public async Task StopBackend() { closing = true; indicator.Close(); await backend.Stop(); IsClosed = true; }
}
