using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;

// Personal planning only. Checklist completion is never learning evidence/mastery.
internal sealed class ErisPlannerItem
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Title { get; set; } = "";
    public string Date { get; set; } = "";
    public string Time { get; set; } = "";
    public bool Done { get; set; }
    public bool Notified { get; set; }

    public DateTime? DueAt => DateTime.TryParseExact($"{Date} {Time}", "yyyy-MM-dd HH:mm",
        CultureInfo.InvariantCulture, DateTimeStyles.None, out var value) ? value : null;
}

internal sealed class ErisPlannerState
{
    public int Version { get; set; } = 1;
    public List<ErisPlannerItem> Goals { get; set; } = new();
    public List<ErisPlannerItem> Reminders { get; set; } = new();
}

internal static class ErisPlannerStore
{
    internal static ErisPlannerState Load(string path)
    {
        if (!File.Exists(path)) return new();
        var state = JsonSerializer.Deserialize<ErisPlannerState>(File.ReadAllText(path))
            ?? throw new InvalidDataException("Dữ liệu kế hoạch trống.");
        if (state.Version != 1) throw new InvalidDataException("Phiên bản kế hoạch chưa được hỗ trợ.");
        state.Goals ??= new();
        state.Reminders ??= new();
        foreach (var item in state.Goals.Concat(state.Reminders))
            if (item == null || string.IsNullOrWhiteSpace(item.Title)
                || !DateTime.TryParseExact(item.Date, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out _))
                throw new InvalidDataException("Có mục kế hoạch không hợp lệ.");
        if (state.Reminders.Any(item => item.DueAt == null))
            throw new InvalidDataException("Có lời nhắc thiếu thời điểm hợp lệ.");
        return state;
    }

    internal static void Save(string path, ErisPlannerState state)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporary, path, true);
    }

    internal static List<ErisPlannerItem> TodayGoals(ErisPlannerState state, DateTime now) =>
        state.Goals.Where(item => item.Date == now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture)).ToList();

    // Return every due item, including ones missed while the app was closed.
    // Caller persists Notified before surfacing, so restarts do not repeat a notice.
    internal static List<ErisPlannerItem> DueReminders(ErisPlannerState state, DateTime now) =>
        state.Reminders.Where(item => !item.Done && !item.Notified && item.DueAt <= now)
            .OrderBy(item => item.DueAt).ToList();

    internal static bool TrySchedule(string date, string time, DateTime now, out DateTime due) =>
        DateTime.TryParseExact($"{date.Trim()} {time.Trim()}", "dd/MM/yyyy HH:mm",
            CultureInfo.InvariantCulture, DateTimeStyles.None, out due) && due > now;
}
