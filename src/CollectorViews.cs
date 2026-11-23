using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Media;

namespace MarketDash
{
    public static class CollectorViews
    {
        static readonly Brush Ink = Brushes.Black;
        static readonly Brush Muted = Tone("#626262");
        static readonly Brush Rule = Tone("#D9D9D9");
        static readonly Brush Raised = Tone("#F6F6F6");

        sealed class CollectorRow
        {
            public int No { get; set; }
            public string File { get; set; }
            public string Name { get; set; }
            public string State { get; set; }
            public string LastRun { get; set; }
            public string NextRun { get; set; }
            public string Message { get; set; }
            public string Search { get; set; }
        }

        static Brush Tone(string value)
        {
            var brush = (SolidColorBrush)new BrushConverter().ConvertFromString(value);
            brush.Freeze();
            return brush;
        }

        static TextBlock Text(string value, double size, Brush color)
        {
            return new TextBlock { Text = value, FontFamily = AppFont.Family, FontSize = size, Foreground = color,
                TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
        }

        static string Safe(string value, string fallback)
        {
            return string.IsNullOrWhiteSpace(value) ? fallback : value;
        }

        static int Count(Dictionary<string, object> data, string key, int maximum)
        {
            var value = Json.Num(data, key);
            return value.HasValue ? (int)Math.Max(0, Math.Min(maximum, value.Value)) : 0;
        }

        static string State(string value, bool running)
        {
            if (string.IsNullOrWhiteSpace(value)) return running ? "대기" : "중지";
            switch (value.Trim().ToLowerInvariant())
            {
                case "running": case "collecting": case "수집 중": case "실행 중": return running ? "수집 중" : "중지";
                case "waiting": case "pending": case "scheduled": case "대기": case "예정": return running ? "대기" : "중지";
                case "ok": case "success": case "completed": case "complete": case "done": case "완료": return "실행 완료";
                case "failed": case "failure": case "error": case "오류": case "실패": return "실행 오류";
                case "timeout": case "timed_out": return "시간 초과";
                case "stopped": case "cancelled": case "canceled": case "중지": return "중지";
                default: return !running && (value.Contains("수집 중") || value.Contains("실행 중")) ? "중지" : value;
            }
        }

        public static FrameworkElement Build(Dictionary<string, object> snapshot, bool running, string error)
        {
            snapshot = snapshot ?? new Dictionary<string, object>();
            var root = new Grid { Background = Brushes.White };
            NameScope.SetNameScope(root, new NameScope());
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            root.RowDefinitions.Add(new RowDefinition());

            int total = Catalog.All.Count;
            int completed = Count(snapshot, "completed", total);
            int active = running ? Count(snapshot, "active", total) : 0;
            var heading = new StackPanel { Margin = new Thickness(0, 0, 0, 12) };
            var title = Text((running ? "수집기 실행 중" : "수집기 중지") + "  ·  완료 " + completed + "/" + total + "  ·  진행 " + active, 20, Ink);
            title.FontWeight = FontWeights.SemiBold;
            heading.Children.Add(title);
            var caption = Text("실행 완료 건수는 수신 성공 건수와 다릅니다. 마지막 상태 기록 " + Tz.Kst(Json.Time(snapshot, "updated_utc"), "MM-dd HH:mm:ss") + " KST", 12, Muted);
            caption.Margin = new Thickness(0, 5, 0, 0);
            caption.ToolTip = caption.Text;
            heading.Children.Add(caption);
            root.Children.Add(heading);

            if (!string.IsNullOrWhiteSpace(error))
            {
                var errorText = Text("실행 상태 안내: " + error.Replace('\r', ' ').Replace('\n', ' '), 12, Ink);
                errorText.ToolTip = error;
                var errorBox = new Border { BorderBrush = Rule, BorderThickness = new Thickness(1), Background = Raised,
                    Padding = new Thickness(9, 7, 9, 7), Margin = new Thickness(0, 0, 0, 12), Child = errorText };
                Grid.SetRow(errorBox, 1);
                root.Children.Add(errorBox);
            }

            var searchRow = new Grid { Margin = new Thickness(0, 0, 0, 10) };
            searchRow.ColumnDefinitions.Add(new ColumnDefinition());
            searchRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var search = new TextBox { Name = "CollectorSearch", FontFamily = AppFont.Family, FontSize = 13, Foreground = Ink, Background = Brushes.White,
                BorderBrush = Rule, BorderThickness = new Thickness(1), Padding = new Thickness(9, 6, 9, 6), ToolTip = "번호·파일명·이름·실행 상태·사유 검색" };
            var count = Text(total + "/" + total + "개 수집기", 12, Muted);
            root.RegisterName("CollectorSearch", search);
            count.Margin = new Thickness(16, 0, 0, 0);
            Grid.SetColumn(count, 1);
            searchRow.Children.Add(search);
            searchRow.Children.Add(count);
            Grid.SetRow(searchRow, 2);
            root.Children.Add(searchRow);

            var byNumber = new Dictionary<int, Dictionary<string, object>>();
            var savedRows = Json.Get(snapshot, "rows") as IEnumerable;
            if (savedRows != null) foreach (object value in savedRows)
            {
                var saved = value as Dictionary<string, object>;
                if (saved == null) continue;
                var no = Json.Num(saved, "no");
                if (no.HasValue && no.Value >= 1 && no.Value <= total && no.Value == Math.Floor(no.Value)) byNumber[(int)no.Value] = saved;
            }

            var rows = new List<CollectorRow>();
            foreach (var source in Catalog.All.OrderBy(s => s.No))
            {
                Dictionary<string, object> saved;
                byNumber.TryGetValue(source.No, out saved);
                var row = new CollectorRow { No = source.No, Name = source.Name + " (" + source.Tag + ")",
                    File = saved == null ? "—" : Safe(Json.Str(saved, "file"), "—"),
                    State = State(saved == null ? null : Json.Str(saved, "state"), running),
                    LastRun = saved == null ? "—" : Tz.Kst(Json.Time(saved, "last_run_utc"), "MM-dd HH:mm:ss"),
                    NextRun = !running || saved == null ? "—" : Tz.Kst(Json.Time(saved, "next_run_utc"), "MM-dd HH:mm:ss"),
                    Message = saved == null ? "아직 실행 기록 없음" : Safe(Json.Str(saved, "message"), "—") };
                row.Search = (row.No.ToString(CultureInfo.InvariantCulture) + " " + row.File + " " + row.Name + " " + row.State + " " + row.Message).ToLowerInvariant();
                rows.Add(row);
            }

            var table = Table();
            root.RegisterName("CollectorTable", table);
            Column(table, "번호", "No", 48);
            Column(table, "파일명", "File", 190);
            Column(table, "이름", "Name", 180);
            Column(table, "실행 상태", "State", 105);
            Column(table, "마지막 실행 KST", "LastRun", 150);
            Column(table, "다음 예정 KST", "NextRun", 150);
            Column(table, "사유", "Message", 1, true);
            table.ItemsSource = rows;
            Grid.SetRow(table, 3);
            root.Children.Add(table);
            search.TextChanged += delegate
            {
                string query = search.Text.Trim().ToLowerInvariant();
                var found = rows.Where(row => query.Length == 0 || row.Search.Contains(query)).ToList();
                table.ItemsSource = found;
                count.Text = found.Count + "/" + total + "개 수집기";
            };
            return root;
        }

        static DataGrid Table()
        {
            var table = new DataGrid { Name = "CollectorTable", FontFamily = AppFont.Family, FontSize = 12, AutoGenerateColumns = false,
                IsReadOnly = true, CanUserAddRows = false, CanUserDeleteRows = false, CanUserResizeRows = false,
                HeadersVisibility = DataGridHeadersVisibility.Column, SelectionMode = DataGridSelectionMode.Single,
                SelectionUnit = DataGridSelectionUnit.FullRow, GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
                HorizontalGridLinesBrush = Rule, RowBackground = Brushes.White, AlternatingRowBackground = Raised,
                Background = Brushes.White, Foreground = Ink, BorderBrush = Rule, BorderThickness = new Thickness(1),
                RowHeaderWidth = 0, MinRowHeight = 30, EnableRowVirtualization = true, EnableColumnVirtualization = true };
            var header = new Style(typeof(DataGridColumnHeader));
            header.Setters.Add(new Setter(Control.BackgroundProperty, Raised));
            header.Setters.Add(new Setter(Control.ForegroundProperty, Muted));
            header.Setters.Add(new Setter(Control.FontFamilyProperty, AppFont.Family));
            header.Setters.Add(new Setter(Control.FontWeightProperty, FontWeights.SemiBold));
            header.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(8)));
            header.Setters.Add(new Setter(Control.BorderBrushProperty, Rule));
            header.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0, 0, 1, 1)));
            table.ColumnHeaderStyle = header;

            var cell = new Style(typeof(DataGridCell));
            cell.Setters.Add(new Setter(Control.BackgroundProperty, Brushes.Transparent));
            cell.Setters.Add(new Setter(Control.ForegroundProperty, Ink));
            cell.Setters.Add(new Setter(Control.BorderBrushProperty, Rule));
            cell.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
            cell.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(7, 5, 7, 5)));
            cell.Setters.Add(new Setter(Control.VerticalContentAlignmentProperty, VerticalAlignment.Center));
            var selected = new Trigger { Property = DataGridCell.IsSelectedProperty, Value = true };
            selected.Setters.Add(new Setter(Control.BackgroundProperty, Tone("#E7E7E7")));
            selected.Setters.Add(new Setter(Control.ForegroundProperty, Ink));
            cell.Triggers.Add(selected);
            table.CellStyle = cell;
            var rowStyle = new Style(typeof(DataGridRow));
            rowStyle.Setters.Add(new Setter(FrameworkElement.ToolTipProperty, new Binding("Message")));
            table.RowStyle = rowStyle;
            return table;
        }

        static void Column(DataGrid table, string title, string property, double width, bool star = false)
        {
            var text = new Style(typeof(TextBlock));
            text.Setters.Add(new Setter(TextBlock.TextTrimmingProperty, TextTrimming.CharacterEllipsis));
            text.Setters.Add(new Setter(TextBlock.VerticalAlignmentProperty, VerticalAlignment.Center));
            table.Columns.Add(new DataGridTextColumn { Header = title, Binding = new Binding(property), ElementStyle = text,
                Width = new DataGridLength(width, star ? DataGridLengthUnitType.Star : DataGridLengthUnitType.Pixel), MinWidth = star ? 130 : 40 });
        }
    }
}
