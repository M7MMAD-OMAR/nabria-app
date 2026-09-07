using System.Globalization;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Media;
using Microsoft.Win32;

namespace Nabria.Desktop;

internal sealed partial class MainWindow
{
    private JsonElement historyItems;
    private string? vocabularyDraft;
    private int setupStep;
    private sealed record Choice(string Id, string Label)
    { public override string ToString() => Label; }

    private TextBlock Heading(string text, double size = 28) => new() { Text = text, TextWrapping = TextWrapping.Wrap, FontSize = size, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 12) };
    private TextBlock Description(string text) => new() { Text = text, TextWrapping = TextWrapping.Wrap, Opacity = .85, LineHeight = 22, Margin = new Thickness(0, 0, 0, 16) };
    private StackPanel Card(StackPanel parent, string title)
    {
        var body = new StackPanel();
        if (title.Length > 0) body.Children.Add(Heading(title, 17));
        var card = new Border { Child = body, CornerRadius = new CornerRadius(8), BorderThickness = new Thickness(1), BorderBrush = new SolidColorBrush(Color.FromArgb(45, 128, 128, 128)), Padding = new Thickness(20), Margin = new Thickness(0, 0, 0, 16) };
        card.SetResourceReference(BackgroundProperty, "CardBackgroundFillColorDefaultBrush");
        parent.Children.Add(card);
        return body;
    }
    private Button Action(string text, Func<Task> action, bool primary = false)
    {
        var button = new Button { Content = text, HorizontalAlignment = HorizontalAlignment.Left };
        button.SetResourceReference(StyleProperty, primary ? "AccentButtonStyle" : "DefaultButtonStyle");
        button.Padding = new Thickness(16, 9, 16, 9); button.Margin = new Thickness(0, 4, 8, 4); button.MinHeight = 38;
        if (primary) { button.Background = SystemColors.HighlightBrush; button.Foreground = SystemColors.HighlightTextBrush; button.FontWeight = FontWeights.SemiBold; }
        button.Click += async (_, _) => { try { await action(); } catch (Exception error) { ShowError(error.Message); } };
        return button;
    }
    private ComboBox Combo(StackPanel parent, string label, IEnumerable<Choice> choices, string selected, Func<string, Task>? changed = null)
    {
        parent.Children.Add(new TextBlock { Text = label, FontWeight = FontWeights.Medium });
        var box = new ComboBox { ItemsSource = choices.ToList(), DisplayMemberPath = "Label", SelectedValuePath = "Id", SelectedValue = selected };
        box.SetResourceReference(StyleProperty, "DefaultComboBoxStyle");
        box.MinHeight = 36; box.Margin = new Thickness(0, 6, 0, 14);
        AutomationProperties.SetName(box, label);
        if (box.SelectedIndex < 0 && box.Items.Count > 0) box.SelectedIndex = 0;
        if (changed != null) box.SelectionChanged += async (_, _) =>
        {
            if (box.SelectedValue is string value) { try { await changed(value); } catch (Exception error) { ShowError(error.Message); } }
        };
        parent.Children.Add(box);
        return box;
    }

    private void HomePage(StackPanel body)
    {
        if (Flag(data, "needsSetup")) { SetupPage(body); return; }
        body.Children.Add(Heading(T("home_title")));
        body.Children.Add(Description(T("home_description")));
        var record = Card(body, "");
        statusLabel = Heading(T("state_" + state), 22); record.Children.Add(statusLabel);
        var row = new StackPanel { Orientation = Orientation.Horizontal };
        recordButton = Action(T(state == "recording" ? "stop" : "start"), async () =>
        {
            string command = state == "recording" ? "stop" : "start";
            WindowState = WindowState.Minimized;
            await Task.Delay(300);
            await Send(command);
        }, primary: true);
        recordButton.MinWidth = 180;
        row.Children.Add(recordButton);
        var cancel = Action(T("cancel"), () => Send("cancel"));
        row.Children.Add(cancel); record.Children.Add(row);
        levelMeter = new ProgressBar { Minimum = 0, Maximum = 60, Height = 5, Margin = new Thickness(0, 16, 0, 16) };
        record.Children.Add(levelMeter);
        record.Children.Add(Description(T("shortcut_help")));
        shortcutLabel = new TextBlock { Text = data.TryGetProperty("shortcuts", out var keys) ? Text(keys, "toggle", T("shortcut_unavailable")) : "", FontWeight = FontWeights.SemiBold, FlowDirection = FlowDirection.LeftToRight, HorizontalAlignment = HorizontalAlignment.Left };
        record.Children.Add(shortcutLabel);
        var latest = Card(body, T("latest"));
        string transcript = historyItems.ValueKind == JsonValueKind.Array && historyItems.GetArrayLength() > 0 ? Text(historyItems[0], "text") : "";
        if (transcript.Length == 0) latest.Children.Add(Description(T("history_empty")));
        lastText = new TextBox { Text = transcript, IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MaxHeight = 160, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, FontSize = 16, BorderThickness = new Thickness(0), Background = Brushes.Transparent };
        latest.Children.Add(lastText);
        latest.Children.Add(Action(T("copy"), () => { if (lastText.Text.Length > 0) Clipboard.SetText(lastText.Text); return Task.CompletedTask; }));
        body.Children.Add(Description(T("minimize_hint")));
    }

    private void SetupPage(StackPanel body)
    {
        body.Children.Add(Heading(T("welcome")));
        body.Children.Add(Description(T("welcome_description")));
        body.Children.Add(Description($"{setupStep + 1} / 3"));
        if (setupStep == 0) LanguageControls(Card(body, T("setup_language")));
        else if (setupStep == 1) ModelControls(Card(body, T("setup_model")));
        else MicrophoneControls(Card(body, T("setup_mic")));
        var navigation = new WrapPanel();
        if (setupStep > 0) navigation.Children.Add(Action(T("back"), () => { setupStep--; Navigate("setup"); return Task.CompletedTask; }));
        var next = Action(T(setupStep == 2 ? "finish_setup" : "next"), async () =>
        {
            if (setupStep < 2) { setupStep++; Navigate("setup"); }
            else await Send("finish_setup");
        }, primary: true);
        next.IsEnabled = !taskRunning && (setupStep == 0 || File.Exists(Setting("model")));
        navigation.Children.Add(next); body.Children.Add(navigation);
        body.Children.Add(Description(T("local_note")));
    }

    private void LanguageControls(StackPanel parent)
    {
        Combo(parent, T("spoken_language"), new[] { new Choice("ar", Strings.T("language.ar.label")), new Choice("en", Strings.T("language.en.label")), new Choice("auto", T("automatic")) }, Setting("language", "ar"), value => Set("language", value));
        Combo(parent, T("interface_language"), new[] { new Choice("auto", T("system_language")), new Choice("ar", Strings.T("language.ar.label")), new Choice("en", Strings.T("language.en.label")) }, Setting("ui_language", "auto"), value => Set("ui_language", value));
    }

    private void ModelControls(StackPanel parent)
    {
        var catalogue = data.GetProperty("models").EnumerateArray().Where(item => !Flag(item, "needsGpu") || Flag(data, "hasGpu")).ToArray();
        var choices = catalogue.Select(item => new Choice(Text(item, "key"), Text(item, "name") + " · " + Text(item, "size") + " MB" + (Flag(item, "installed") ? " · " + T("installed") : ""))).ToList();
        string current = Path.GetFileNameWithoutExtension(Setting("model")).Replace("ggml-", "");
        if (!choices.Any(item => item.Id == current)) current = Text(data, "recommended", "base");
        var selector = Combo(parent, T("speech_model"), choices, current);
        selector.FlowDirection = FlowDirection.LeftToRight;
        parent.Children.Add(Description(T(Flag(data, "hasGpu") ? "model_gpu_help" : "model_cpu_help")));
        var actions = new WrapPanel();
        var download = Action(T("use_model"), async () =>
        {
            if (selector.SelectedValue is not string selected || taskRunning) return;
            bool installed = catalogue.Any(item => Text(item, "key") == selected && Flag(item, "installed"));
            taskRunning = true; taskText = installed ? "verifying" : "downloading"; Navigate(page);
            await Send(installed ? "select_model" : "download", new() { ["model"] = selected });
        }, primary: true);
        download.IsEnabled = !taskRunning; actions.Children.Add(download);
        var import = Action(T("import_model"), async () =>
        {
            var picker = new OpenFileDialog { Title = T("import_model"), Filter = T("model_filter"), CheckFileExists = true };
            if (picker.ShowDialog(this) != true) return;
            taskRunning = true; taskText = "verifying"; Navigate(page);
            await Send("adopt", new() { ["path"] = picker.FileName });
        });
        import.IsEnabled = !taskRunning; actions.Children.Add(import); parent.Children.Add(actions);
        if (data.TryGetProperty("found", out var found) && found.GetArrayLength() > 0)
        {
            parent.Children.Add(Description(T("found_model")));
            foreach (var item in found.EnumerateArray().Take(3))
            {
                string path = Text(item, "path");
                var use = Action(Text(item, "name"), async () => { taskRunning = true; taskText = "verifying"; Navigate(page); await Send("adopt", new() { ["path"] = path }); });
                use.ToolTip = path; use.IsEnabled = !taskRunning; parent.Children.Add(use);
            }
        }
        progress = new ProgressBar { Height = 6, Minimum = 0, Maximum = 100, Margin = new Thickness(0, 12, 0, 8), IsIndeterminate = taskRunning && !micTesting };
        parent.Children.Add(progress);
        taskLabel = Description(taskRunning && !micTesting ? T(taskText) : ""); parent.Children.Add(taskLabel);
        if (taskRunning && !micTesting) parent.Children.Add(Action(T("cancel"), () => Send("cancel_task")));
    }

    private void MicrophoneControls(StackPanel parent)
    {
        var devices = data.GetProperty("devices").EnumerateArray().ToArray();
        string selected = Setting("input_device", devices.Where(item => Flag(item, "default")).Select(item => Text(item, "name")).FirstOrDefault() ?? "");
        if (devices.Length == 0) parent.Children.Add(Description(T("no_microphone")));
        else
        {
            var device = Combo(parent, T("microphone"), devices.Select(item => new Choice(Text(item, "name"), Text(item, "name"))), selected, value => Set("input_device", value));
            device.FlowDirection = FlowDirection.LeftToRight;
        }
        var row = new WrapPanel();
        var test = Action(T("test_microphone"), async () => { taskRunning = true; micTesting = true; micText = "speak_test"; Navigate(page); await Send("mic_test"); });
        test.IsEnabled = !taskRunning && devices.Length > 0;
        row.Children.Add(test); row.Children.Add(Action(T("refresh_devices"), () => Send("refresh"))); parent.Children.Add(row);
        levelMeter = new ProgressBar { Minimum = 0, Maximum = 60, Height = 6, Margin = new Thickness(0, 12, 0, 8) };
        parent.Children.Add(levelMeter);
        micResult = Description(micText.Length > 0 ? T(micText) : ""); parent.Children.Add(micResult);
    }

    private void SettingsPage(StackPanel body)
    {
        body.Children.Add(Heading(T("settings")));
        body.Children.Add(Description(T("settings_description")));
        LanguageControls(Card(body, T("languages")));
        MicrophoneControls(Card(body, T("microphone")));
        ModelControls(Card(body, T("speech_model")));
        var words = Card(body, T("vocabulary"));
        words.Children.Add(Description(T("vocabulary_help")));
        var vocabulary = new TextBox { Text = vocabularyDraft ?? Setting("vocabulary"), AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, MinHeight = 80, MaxHeight = 180, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        vocabulary.TextChanged += (_, _) => vocabularyDraft = vocabulary.Text;
        words.Children.Add(vocabulary);
        words.Children.Add(Action(T("save"), async () => { string value = vocabulary.Text; vocabularyDraft = null; await Set("vocabulary", value); }));
        var privacy = Card(body, T("privacy"));
        privacy.Children.Add(Description(T("privacy_help")));
        var retain = new CheckBox { Content = T("keep_audio"), IsChecked = Flag(Settings, "keep_audio"), Margin = new Thickness(0, 4, 0, 8) };
        retain.Click += async (_, _) => await Set("keep_audio", retain.IsChecked == true);
        privacy.Children.Add(retain);
    }

    private void HistoryPage(StackPanel body)
    {
        body.Children.Add(Heading(T("history")));
        body.Children.Add(Description(T("history_description")));
        if (historyItems.ValueKind != JsonValueKind.Array || historyItems.GetArrayLength() == 0)
        { body.Children.Add(Description(T("history_empty"))); return; }
        body.Children.Add(Action(T("clear_history"), async () =>
        {
            if (MessageBox.Show(this, T("clear_history_confirm"), T("clear_history"), MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No) == MessageBoxResult.Yes) await Send("clear_history");
        }));
        foreach (var item in historyItems.EnumerateArray())
        {
            string date = DateTime.TryParse(Text(item, "at"), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var when) ? when.ToString("dd MMM yyyy · h:mm tt", CultureInfo.CurrentCulture) : Text(item, "at");
            var entry = Card(body, date);
            string text = Text(item, "text");
            entry.Children.Add(new TextBox { Text = text, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true, MaxHeight = 180, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, BorderThickness = new Thickness(0), Background = Brushes.Transparent, FontSize = 16 });
            entry.Children.Add(Action(T("copy"), () => { Clipboard.SetText(text); return Task.CompletedTask; }));
        }
    }

    private void HelpPage(StackPanel body)
    {
        body.Children.Add(Heading(T("help")));
        body.Children.Add(Description(T("help_description")));
        var how = Card(body, T("how_to"));
        how.Children.Add(Description(T("how_to_steps")));
        how.Children.Add(Description(T("minimize_hint")));
        var links = Card(body, T("support"));
        links.Children.Add(Action(T("report_issue"), () => { Open("https://github.com/M7MMAD-OMAR/nabria-app/issues"); return Task.CompletedTask; }));
        links.Children.Add(Action(T("support_project"), () => { Open("https://www.buymeacoffee.com/m7mmadomar"); return Task.CompletedTask; }));
        links.Children.Add(Action(T("sbarah_site"), () => { Open("https://sbarah.com"); return Task.CompletedTask; }));
        body.Children.Add(Description(T("local_note")));
        string version = File.Exists(Path.Combine(AppContext.BaseDirectory, "version.txt")) ? File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "version.txt")).Trim() : "";
        body.Children.Add(new TextBlock { Text = "Nabria " + version, Opacity = .6, FlowDirection = FlowDirection.LeftToRight });
    }
}
