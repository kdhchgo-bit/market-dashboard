using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;

namespace MarketDash
{
    public static class DetailViews
    {
        static readonly Brush Ink = Brush("#141414"), Muted = Brush("#626262"), Rule = Brush("#D9D9D9");
        static readonly Brush Cyan = Brush("#374151"), Amber = Brush("#303030"), Red = Brush("#303030");
        static readonly Brush Panel = Brush("#FFFFFF"), Raised = Brush("#F6F6F6");
        static readonly CultureInfo Ci = CultureInfo.InvariantCulture;
        static Brush Brush(string hex) { var b = (SolidColorBrush)new BrushConverter().ConvertFromString(hex); b.Freeze(); return b; }
        static Brush StateBrush(int state) { return state > 0 ? Cyan : state == 0 ? Amber : Muted; }
        static string Safe(string text, string missing = "미수신") { return string.IsNullOrWhiteSpace(text) ? missing : text; }
        static string Pct(double? value) { return value.HasValue ? (value.Value * 100).ToString("+0.00;-0.00;0.00", Ci) + "%" : "미수신"; }
        static string Mult(double? value) { return value.HasValue ? value.Value.ToString("0.00", Ci) + "배" : "이력 부족"; }
        static string Compare(double? left, double? right) { return !left.HasValue || !right.HasValue ? " ? " : left.Value > right.Value ? " > " : " ≤ "; }
        static TextBlock Text(string value, double size = 13, Brush color = null, bool wrap = false)
        {
            return new TextBlock { FontFamily = AppFont.Family, Text = Safe(value), FontSize = size, Foreground = color ?? Ink, TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap,
                TextTrimming = wrap ? TextTrimming.None : TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
        }
        static FrameworkElement Line(string title, string value, int state, bool wrap = false)
        {
            var grid = new Grid { Margin = new Thickness(0, 0, 0, 7) };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(4) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.Children.Add(new Border { Background = StateBrush(state), Margin = new Thickness(0, 2, 0, 2) });
            var stack = new StackPanel { Margin = new Thickness(9, 0, 0, 0) };
            stack.Children.Add(Text(title, 12, Muted));
            var detail = Text(value, 13, StateBrush(state), wrap); detail.Margin = new Thickness(0, 2, 0, 0); detail.ToolTip = value; stack.Children.Add(detail);
            Grid.SetColumn(stack, 1); grid.Children.Add(stack); return grid;
        }
        static void Heading(Panel host, string value)
        {
            var text = Text(value, 13, Muted); text.Margin = new Thickness(0, 8, 0, 6); host.Children.Add(text);
        }
        static int CandidateState(Candidate c, int step)
        {
            if (c.Stage > step) return 1;
            if (c.Stage < step) return -1;
            return c.Reason != null && (c.Reason.Contains("미수신") || c.Reason.Contains("이력 부족") || c.Reason.Contains("확인 대기")) ? -1 : 0;
        }

        public static void Render(Panel checks, Panel detail, Model m, string groupKey, bool coin, string ticker, Action<string> selectTicker)
        {
            checks.Children.Clear(); detail.Children.Clear();
            if (m == null) { detail.Children.Add(Text("저장 자료를 읽는 중입니다.", 13, Muted)); return; }
            var all = m.Candidates ?? new List<Candidate>();
            var list = all.Where(c => string.IsNullOrEmpty(groupKey) || (c.Group == groupKey && (c.Market == "코인") == coin))
                .OrderByDescending(c => c.Stage).ThenByDescending(c => c.VolMult ?? -1).ThenByDescending(c => c.Excess ?? -99).ThenBy(c => c.Ticker, StringComparer.Ordinal).ToList();
            var selected = all.FirstOrDefault(c => !string.IsNullOrEmpty(ticker) && c.Ticker == ticker);
            if (selected == null) selected = !string.IsNullOrEmpty(groupKey) ? list.FirstOrDefault() : m.Winner ?? m.Watch ?? list.FirstOrDefault();
            bool marketCoin = selected != null ? selected.Market == "코인" : coin;
            var relevant = (m.Checks ?? new List<Check>()).Where(c => c.Step != (marketCoin ? "미국 레짐" : "코인 레짐")).ToList();
            foreach (string step in new[] { "일정", "지정학", marketCoin ? "코인 레짐" : "미국 레짐" })
            {
                var rows = relevant.Where(c => c.Step == step).ToList();
                var chosen = rows.OrderBy(c => c.State == 0 ? 0 : c.State < 0 ? 1 : 2).FirstOrDefault();
                if (chosen != null) checks.Children.Add(Line(step + " · " + chosen.Item, chosen.Value, chosen.State));
            }
            if (selected == null)
            {
                detail.Children.Add(Line("관망 사유", Safe(m.Reason, "구성 종목 없음"), 0, true));
                detail.Children.Add(Text("이 그룹에는 수집 대상 개별 종목이 없습니다.", 12, Muted, true)); return;
            }
            var identity = Text(selected.Ticker + "  " + Safe(selected.Name, ""), 20, Ink); identity.FontWeight = FontWeights.SemiBold;
            detail.Children.Add(identity);
            var context = Text(selected.Market + " · " + selected.GroupName + " · 기준 " + selected.Bench, 12, Muted); context.Margin = new Thickness(0, 3, 0, 8); detail.Children.Add(context);
            detail.Children.Add(Line(selected.Pass ? "최종 통과" : "현재 보류", Safe(selected.Reason, m.Reason), selected.Pass ? 1 : 0, true));
            string strengths = selected.Ticker + " " + Pct(selected.R1h) + Compare(selected.R1h, selected.G1h) + selected.GroupName + " " + Pct(selected.G1h) + Compare(selected.G1h, selected.B1h) + selected.Bench + " " + Pct(selected.B1h) + Compare(selected.B1h, 0) + "0";
            detail.Children.Add(Line("상대강도 · 1시간 수익률", strengths, CandidateState(selected, 2), true));
            string volume = Mult(selected.VolMult) + " · 동시간대 " + selected.VolSamples + "/" + Cfg.VolLookback + "일" + (selected.VolEst ? " · 종가×거래량 추정" : "");
            detail.Children.Add(Line("거래대금 · 5분봉 / " + Cfg.VolLookback + "일 중앙값", volume, CandidateState(selected, 3), true));
            string spread = selected.Spread.HasValue ? "호가 스프레드 중앙값 " + (selected.Spread.Value * 100).ToString("0.0", Ci) + "% / 한도 " + (Cfg.SpreadMax * 100).ToString("0.0", Ci) + "%" : selected.Stage < 4 ? "선행 단계 보류" : "호가 확인 대기";
            detail.Children.Add(Line("옵션 · " + Safe(selected.Chain) + " / " + Safe(selected.Relation), spread + "\n" + Safe(selected.ChainNote, selected.Stage < 4 ? "선행 단계 통과 후 확인" : "미수신"), CandidateState(selected, 4), true));
            var checksExpander = new Expander { Header = "시장 판정 상세 " + relevant.Count + "건", Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 5, 0, 4) };
            var extra = new StackPanel { Margin = new Thickness(2, 8, 0, 0) };
            foreach (var check in relevant) extra.Children.Add(Line(check.Step + " · " + check.Item, check.Value, check.State, true));
            checksExpander.Content = extra; detail.Children.Add(checksExpander);
            Heading(detail, string.IsNullOrEmpty(groupKey) ? "전체 후보 · " + list.Count + "종목" : "그룹 구성 종목 · " + list.Count + "종목");
            if (list.Count == 0) detail.Children.Add(Text("수집 대상 개별 종목 없음", 12, Muted));
            foreach (var candidate in list)
            {
                var local = candidate;
                var row = new StackPanel();
                row.Children.Add(Text(candidate.Ticker + "  " + Mult(candidate.VolMult) + "  " + (candidate.Pass ? "통과" : "보류"), 13, candidate.Pass ? Cyan : Ink));
                row.Children.Add(Text(candidate.Reason, 11, Muted));
                var button = new Button { Content = row, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(8, 6, 8, 6), Margin = new Thickness(0, 0, 0, 4),
                    Background = candidate.Ticker == selected.Ticker ? Brush("#E5E7EB") : Raised, BorderBrush = candidate.Ticker == selected.Ticker ? Cyan : Rule,
                    ToolTip = candidate.Name + " · " + candidate.Reason };
                button.Click += delegate { if (selectTicker != null) selectTicker(local.Ticker); };
                detail.Children.Add(button);
            }
        }

        sealed class StatusItem
        {
            public int No { get; set; }
            public string Tag { get; set; }
            public string Name { get; set; }
            public string Provider { get; set; }
            public string Observed { get; set; }
            public string State { get; set; }
            public string Count { get; set; }
            public string Reason { get; set; }
            public string Search { get; set; }
        }
        static DataGrid Table()
        {
            var grid = new DataGrid { FontFamily = AppFont.Family, AutoGenerateColumns = false, IsReadOnly = true, CanUserAddRows = false, CanUserDeleteRows = false,
                CanUserResizeRows = false, HeadersVisibility = DataGridHeadersVisibility.Column, SelectionMode = DataGridSelectionMode.Single,
                SelectionUnit = DataGridSelectionUnit.FullRow, GridLinesVisibility = DataGridGridLinesVisibility.Horizontal,
                HorizontalGridLinesBrush = Rule, RowBackground = Panel, AlternatingRowBackground = Raised, Foreground = Ink, Background = Panel,
                BorderBrush = Rule, FontSize = 12, RowHeaderWidth = 0, MinRowHeight = 29, EnableRowVirtualization = true, EnableColumnVirtualization = true };
            var header = new Style(typeof(DataGridColumnHeader));
            header.Setters.Add(new Setter(Control.BackgroundProperty, Raised));
            header.Setters.Add(new Setter(Control.ForegroundProperty, Muted));
            header.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(8, 8, 8, 8)));
            header.Setters.Add(new Setter(Control.BorderBrushProperty, Rule));
            header.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0, 0, 1, 1))); grid.ColumnHeaderStyle = header;
            var cell = new Style(typeof(DataGridCell));
            cell.Setters.Add(new Setter(Control.PaddingProperty, new Thickness(6, 4, 6, 4)));
            cell.Setters.Add(new Setter(Control.BorderThicknessProperty, new Thickness(0)));
            cell.Setters.Add(new Setter(Control.VerticalContentAlignmentProperty, VerticalAlignment.Center));
            var selectedCell = new Trigger { Property = DataGridCell.IsSelectedProperty, Value = true };
            selectedCell.Setters.Add(new Setter(Control.BackgroundProperty, Brush("#E5E7EB")));
            selectedCell.Setters.Add(new Setter(Control.ForegroundProperty, Ink)); cell.Triggers.Add(selectedCell); grid.CellStyle = cell;
            return grid;
        }
        static void Column(DataGrid grid, string title, string path, double width, bool star = false)
        {
            grid.Columns.Add(new DataGridTextColumn { Header = title, Binding = new Binding(path), Width = new DataGridLength(width, star ? DataGridLengthUnitType.Star : DataGridLengthUnitType.Pixel) });
        }
        public static FrameworkElement Status(Model m)
        {
            var root = new Grid(); root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto }); root.RowDefinitions.Add(new RowDefinition());
            var searchRow = new Grid { Margin = new Thickness(0, 0, 0, 12) }; searchRow.ColumnDefinitions.Add(new ColumnDefinition()); searchRow.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var search = new TextBox { Name = "StatusSearch", FontFamily = AppFont.Family, Background = Raised, Foreground = Ink, BorderBrush = Rule, Padding = new Thickness(9, 6, 9, 6), FontSize = 13, ToolTip = "번호·종목·이름·공급처·상태·사유 검색" };
            var count = Text("102개 원천 · KST", 12, Muted); count.Margin = new Thickness(16, 0, 0, 0); Grid.SetColumn(count, 1); searchRow.Children.Add(search); searchRow.Children.Add(count); root.Children.Add(searchRow);
            var rows = new List<StatusItem>();
            foreach (var source in Catalog.All)
            {
                var status = m == null ? null : m.Status.FirstOrDefault(s => s.Src.No == source.No);
                var row = new StatusItem { No = source.No, Tag = source.Tag, Name = source.Name, Provider = status == null ? "확인 대기" : Safe(status.Provider, "확인 대기"),
                    Observed = status == null ? "—" : Safe(status.LastObs, "—"), State = status == null ? "미수신" : Safe(status.State),
                    Count = status == null ? "0 / 0" : status.Valid.ToString(Ci) + " / " + status.Total.ToString(Ci), Reason = status == null ? "원천 폴더 또는 저장 행 없음" : Safe(status.Reason, "사용 가능") };
                row.Search = (row.No + " " + row.Tag + " " + row.Name + " " + row.Provider + " " + row.State + " " + row.Reason).ToLowerInvariant(); rows.Add(row);
            }
            var table = Table(); Column(table, "번호", "No", 50); Column(table, "원천", "Tag", 125); Column(table, "이름", "Name", 150);
            Column(table, "공급처·주기", "Provider", 210); Column(table, "마지막 원본 KST", "Observed", 145); Column(table, "상태", "State", 120);
            Column(table, "유효 / 전체", "Count", 100); Column(table, "제외 사유", "Reason", 1, true);
            table.ItemsSource = rows; Grid.SetRow(table, 1); root.Children.Add(table);
            search.TextChanged += delegate { var q = search.Text.Trim().ToLowerInvariant(); var found = rows.Where(r => q.Length == 0 || r.Search.Contains(q)).ToList(); table.ItemsSource = found; count.Text = found.Count + "/102개 원천 · KST"; };
            return root;
        }

        public static FrameworkElement Risks(Model m)
        {
            var stack = new StackPanel();
            stack.Children.Add(Text("지표는 각각의 원본 관측일 기준입니다. 매수 순위에는 가산하지 않습니다.", 13, Muted, true));
            if (m != null) foreach (var risk in m.Risk) stack.Children.Add(Line(risk.Label, Safe(risk.Value) + (string.IsNullOrWhiteSpace(risk.Note) ? "" : "\n" + risk.Note), risk.Sign == 0 ? -1 : risk.Sign > 0 ? 1 : 0, true));
            return Scroll(stack);
        }
        public static FrameworkElement Events(Model m)
        {
            var stack = new StackPanel();
            stack.Children.Add(Text("확인된 발표 " + Cfg.EventBeforeMin + "분 전부터 " + Cfg.EventAfterMin + "분 후까지 두 시장을 보류합니다. 시각은 KST입니다.", 13, Muted, true));
            if (m != null)
            {
                foreach (var warning in m.EventWarnings) stack.Children.Add(Line("시각 확인 대기", warning, 0, true));
                foreach (var item in m.Events.OrderBy(e => e.WhenUtc ?? e.Date)) stack.Children.Add(EventLine(item, false));
                if (m.Events.Count == 0) stack.Children.Add(Text("확인된 발표 일정 미수신", 13, Amber));
            }
            return Scroll(stack);
        }
        public static FrameworkElement News(Model m)
        {
            var stack = new StackPanel();
            if (m == null || m.News.Count == 0) stack.Children.Add(Text("수신된 헤드라인 없음", 13, Muted));
            else foreach (var item in m.News) stack.Children.Add(NewsLine(item, false));
            return Scroll(stack);
        }
        static FrameworkElement Scroll(FrameworkElement child) { return new ScrollViewer { Content = child, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled }; }
        static FrameworkElement EventLine(EventItem item, bool compact)
        {
            var when = item.WhenUtc.HasValue ? Tz.Kst(item.WhenUtc, "MM-dd HH:mm") : item.Date.ToString("MM-dd", Ci) + " 시각 미정";
            var grid = new Grid { Margin = new Thickness(0, compact ? 4 : 9, 0, compact ? 4 : 9) };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(compact ? 112 : 145) }); grid.ColumnDefinitions.Add(new ColumnDefinition());
            var time = Text(when, compact ? 11 : 13, item.Today ? Amber : Muted); time.FontFamily = AppFont.Family;
            var title = Text(Safe(item.Region, "") + " " + item.Name, compact ? 12 : 14, Ink, !compact); title.ToolTip = item.Name + " · " + item.Raw;
            Grid.SetColumn(title, 1); grid.Children.Add(time); grid.Children.Add(title); return grid;
        }
        static FrameworkElement NewsLine(NewsItem item, bool compact)
        {
            var row = new StackPanel { Margin = new Thickness(0, 3, 0, compact ? 4 : 12), Cursor = Cursors.Hand, ToolTip = item.Title + "\n" + Safe(item.Link, "원문 주소 없음") };
            var title = Text(item.Title, compact ? 12 : 15, item.Flag ? Amber : Ink, !compact); row.Children.Add(title);
            row.Children.Add(Text(item.Src + "  " + Tz.Kst(item.Pub, "MM-dd HH:mm") + " KST", compact ? 10 : 12, Muted));
            row.MouseLeftButtonUp += delegate(object sender, MouseButtonEventArgs e)
            {
                e.Handled = true;
                Uri uri;
                if (Uri.TryCreate(item.Link, UriKind.Absolute, out uri) && (uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp))
                {
                    try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
                    catch (Exception ex) { MessageBox.Show("원문을 열지 못했습니다.\n" + ex.Message, "헤드라인 원문", MessageBoxButton.OK, MessageBoxImage.Information); }
                }
            };
            return row;
        }
        public static void FillEvents(Panel host, Model m)
        {
            host.Children.Clear(); if (m == null) return;
            int n = 0;
            if (m.EventWarnings.Count > 0) { var warning = Text(m.EventWarnings[0], 11, Amber); warning.Margin = new Thickness(0, 3, 0, 4); warning.ToolTip = string.Join("\n", m.EventWarnings); host.Children.Add(warning); n++; }
            foreach (var item in m.Events.Where(e => !e.WhenUtc.HasValue || e.WhenUtc.Value >= m.DecidedUtc.AddMinutes(-Cfg.EventAfterMin)).OrderBy(e => e.WhenUtc ?? e.Date).Take(4 - n)) host.Children.Add(EventLine(item, true));
            if (host.Children.Count == 0) host.Children.Add(Text("다음 발표 일정 미수신", 12, Muted));
        }
        public static void FillNews(Panel host, Model m)
        {
            host.Children.Clear(); if (m == null) return;
            foreach (var item in m.News.Take(3)) host.Children.Add(NewsLine(item, true));
            if (host.Children.Count == 0) host.Children.Add(Text("수신된 헤드라인 없음", 12, Muted));
        }
        public static void FillRisk(Panel host, Model m)
        {
            host.Children.Clear(); if (m == null) return;
            var priority = new[] { "VIX9D/3M", "풋/콜 전체", "풋/콜 개별주", "풋/콜 지수", "SKEW", "VVIX", "BTC IV", "ETH IV", "다음 만기" };
            foreach (var key in priority)
            {
                var risk = m.Risk.FirstOrDefault(r => r.Label != null && r.Label.StartsWith(key, StringComparison.Ordinal));
                var box = new StackPanel { Margin = new Thickness(0, 2, 10, 3) };
                box.Children.Add(Text(risk == null ? key : risk.Label, 10, Muted));
                var value = Text(risk == null ? "미수신" : risk.Value, 12, risk != null && risk.Sign < 0 ? Amber : Ink); value.Margin = new Thickness(0, 2, 0, 0); box.Children.Add(value);
                if (risk != null) box.ToolTip = risk.Label + "\n" + risk.Value + (string.IsNullOrWhiteSpace(risk.Note) ? "" : "\n" + risk.Note);
                host.Children.Add(box);
            }
        }
    }
}
