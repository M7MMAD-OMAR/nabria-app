using System.Globalization;
using System.IO;
using System.Text.Json;

namespace Nabria.Desktop;

internal static class Strings
{
    private static readonly Dictionary<string, Dictionary<string, string>> Table =
        JsonSerializer.Deserialize<Dictionary<string, Dictionary<string, string>>>(
            File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "strings.json")))!;
    public static string Language { get; set; } = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "ar" ? "ar" : "en";
    public static bool IsRtl => Language == "ar";
    public static string T(string key) => Table.TryGetValue(key, out var entry) ? entry.GetValueOrDefault(Language, entry["en"]) : key;
}
