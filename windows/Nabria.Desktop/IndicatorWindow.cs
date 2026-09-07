using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Media;

namespace Nabria.Desktop;

internal sealed class IndicatorWindow : Window
{
    private readonly TextBlock label = new() { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(8, 0, 12, 0), Foreground = Brushes.White };
    private readonly ProgressBar meter = new() { Width = 36, Height = 7, Minimum = 0, Maximum = 60, Margin = new Thickness(0, 0, 8, 0) };
    private readonly Button stop, cancel;
    private string language = "";
    public IndicatorWindow(Func<string, Task> send)
    {
        Width = 310; Height = 62; WindowStyle = WindowStyle.None; ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false; ShowActivated = false; Topmost = true;
        Background = new SolidColorBrush(Color.FromRgb(32, 34, 38));
        var row = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(12, 6, 8, 6) };
        row.Children.Add(meter); row.Children.Add(label);
        stop = new Button { Content = "■", ToolTip = Strings.T("desktop.stop"), Padding = new Thickness(12, 4, 12, 4) };
        AutomationProperties.SetName(stop, Strings.T("desktop.stop"));
        stop.Click += async (_, _) => await send("stop");
        row.Children.Add(stop);
        cancel = new Button { Content = "×", ToolTip = Strings.T("desktop.cancel"), Padding = new Thickness(12, 4, 12, 4) };
        AutomationProperties.SetName(cancel, Strings.T("desktop.cancel"));
        cancel.Click += async (_, _) => await send("cancel");
        row.Children.Add(cancel);
        Content = row;
        SourceInitialized += (_, _) => Native.NoActivate(this);
    }
    public void Update(string state, double level)
    {
        if (language != Strings.Language)
        {
            language = Strings.Language;
            stop.ToolTip = Strings.T("desktop.stop"); cancel.ToolTip = Strings.T("desktop.cancel");
            AutomationProperties.SetName(stop, Strings.T("desktop.stop"));
            AutomationProperties.SetName(cancel, Strings.T("desktop.cancel"));
        }
        FlowDirection = Strings.IsRtl ? FlowDirection.RightToLeft : FlowDirection.LeftToRight;
        label.Text = Strings.T("desktop.state_" + state);
        stop.IsEnabled = state == "recording";
        meter.IsIndeterminate = state == "working";
        meter.Value = Math.Clamp(level + 60, 0, 60);
        if (state == "idle") { Hide(); return; }
        var work = SystemParameters.WorkArea;
        Left = work.Right - Width - 24; Top = work.Bottom - Height - 24;
        if (!IsVisible) Show();
    }
}
