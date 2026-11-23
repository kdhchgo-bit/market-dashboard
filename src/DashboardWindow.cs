using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Threading;

namespace MarketDash
{
    public class DashboardWindow : Window
    {
        readonly Grid view;
        readonly AppSettings settings;
        readonly DispatcherTimer clock;
        readonly RelativeChart sectors = new RelativeChart();
        readonly RelativeChart themes = new RelativeChart();
        readonly CollectorService collector = new CollectorService();
        DataStore store;
        Model model;
        SetupWindow setupWindow;
        bool busy, pending, closed, fullscreen = true, selectedCoin;
        string selectedGroup, selectedTicker, overlayKind;
        DateTime nextRun = DateTime.MaxValue;
        DateTime lastDataPoll = DateTime.MinValue;
        DateTime lastOverlayRefresh = DateTime.MinValue;
        double collectorChanged = -1;
        string collectorError;
        static readonly Brush Ink = BrushOf("#161616");
        static readonly Brush Muted = BrushOf("#666666");
        static readonly Brush Green = BrushOf("#222222");
        static readonly Brush Amber = BrushOf("#333333");
        static readonly Brush Red = BrushOf("#922E2E");

        public DashboardWindow()
        {
            Title = "시장 관측실 · 미국 주식 / 코인";
            Background = Brushes.White;
            FontFamily = AppFont.Family;
            Application.Current.Resources["AppFont"] = AppFont.Family;
            Foreground = Ink;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            Width = Math.Min(1420, SystemParameters.WorkArea.Width * 0.94);
            Height = Math.Min(864, SystemParameters.WorkArea.Height * 0.94);
            MinWidth = 1000;
            MinHeight = 700;
            using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("MarketDash.Dashboard.xaml"))
            {
                if (stream == null) throw new InvalidOperationException("대시보드 화면 리소스가 없습니다.");
                view = (Grid)XamlReader.Load(stream);
            }
            Content = view;
            settings = AppSettings.Load();
            N<Grid>("SectorChartHost").Children.Add(sectors);
            N<Grid>("ThemeChartHost").Children.Add(themes);
            sectors.Selected += delegate(string key) { SelectGroup(key, false); };
            themes.Selected += delegate(string key) { SelectGroup(key, true); };
            N<Button>("FolderButton").Click += delegate { OpenDataFolder(); };
            N<Button>("RefreshButton").Click += delegate { CollectNow(); };
            N<Button>("CollectButton").Click += delegate { ToggleCollectors(); };
            N<Button>("CollectorStatusButton").Click += delegate { OpenOverlay("collectors"); };
            N<Button>("RankingsButton").Click += delegate
            {
                CloseOverlay();
                OpenSetup();
            };
            N<Button>("FullscreenButton").Click += delegate { ToggleFullscreen(); };
            N<Button>("CloseButton").Click += delegate { Close(); };
            N<Button>("StatusButton").Click += delegate { OpenOverlay("status"); };
            N<Button>("OverlayCloseButton").Click += delegate { CloseOverlay(); };
            N<Panel>("MacroHost").MouseLeftButtonUp += delegate { OpenOverlay("macro"); };
            N<Panel>("EventsHost").MouseLeftButtonUp += delegate { OpenOverlay("events"); };
            N<Panel>("NewsHost").MouseLeftButtonUp += delegate { OpenOverlay("news"); };
            N<Panel>("RiskHost").MouseLeftButtonUp += delegate { OpenOverlay("risk"); };
            foreach (string name in new[] { "MacroHost", "EventsHost", "NewsHost", "RiskHost" })
            {
                N<Panel>(name).Background = Brushes.Transparent;
                N<Panel>(name).Cursor = Cursors.Hand;
                N<Panel>(name).ToolTip = "클릭하여 전체 자료 보기";
            }
            KeyDown += OnKeyDown;
            clock = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
            clock.Tick += delegate { Tick(); };
            Loaded += delegate
            {
                SetFullscreen(true);
                clock.Start();
                StartCollectors();
                if (!busy) RefreshData();
                Tick();
            };
            Closed += delegate { closed = true; clock.Stop(); collector.Dispose(); };
        }

        T N<T>(string name) where T : FrameworkElement
        {
            var element = view.FindName(name) as T;
            if (element == null) throw new InvalidOperationException("화면 요소가 없습니다: " + name);
            return element;
        }

        static Brush BrushOf(string hex)
        {
            var b = (SolidColorBrush)new BrushConverter().ConvertFromInvariantString(hex);
            b.Freeze();
            return b;
        }

        void Txt(string name, string text, Brush color = null)
        {
            var t = N<TextBlock>(name);
            t.Text = text ?? "—";
            t.ToolTip = text;
            if (color != null) t.Foreground = color;
        }

        void OnKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.F11) { ToggleFullscreen(); e.Handled = true; }
            else if (e.Key == Key.F5) { CollectNow(); e.Handled = true; }
            else if (e.Key == Key.O && Keyboard.Modifiers == ModifierKeys.Control) { OpenDataFolder(); e.Handled = true; }
            else if (e.Key == Key.Escape)
            {
                if (overlayKind != null) CloseOverlay();
                else if (selectedGroup != null || selectedTicker != null)
                {
                    selectedGroup = selectedTicker = null;
                    RenderSelection();
                }
                else if (fullscreen) SetFullscreen(false);
                e.Handled = true;
            }
        }

        void ToggleFullscreen() { SetFullscreen(!fullscreen); }

        void SetFullscreen(bool value)
        {
            fullscreen = value;
            WindowState = WindowState.Normal;
            WindowStyle = value ? WindowStyle.None : WindowStyle.SingleBorderWindow;
            ResizeMode = value ? ResizeMode.NoResize : ResizeMode.CanResize;
            if (value) WindowState = WindowState.Maximized;
            N<Button>("FullscreenButton").Content = value ? "창 모드 · F11" : "전체화면 · F11";
        }

        void OpenDataFolder()
        {
            Directory.CreateDirectory(settings.DataRoot);
            Process.Start(new ProcessStartInfo("explorer.exe", "\"" + settings.DataRoot + "\"") { UseShellExecute = true });
        }

        void StartCollectors()
        {
            try { collector.Start(); collectorError = null; collectorChanged = -1; }
            catch (Exception ex) { collectorError = ex.Message; }
            UpdateCollectorStatus();
        }

        void ToggleCollectors()
        {
            if (collector.IsRunning) { collector.Stop(); collectorError = null; }
            else StartCollectors();
            UpdateCollectorStatus();
            if (overlayKind == "collectors") OpenOverlay("collectors");
        }

        void CollectNow()
        {
            try { collector.CollectNow(); collectorError = null; }
            catch (Exception ex) { collectorError = ex.Message; }
            RefreshData();
            UpdateCollectorStatus();
        }

        void UpdateCollectorStatus()
        {
            var s = collector.ReadState();
            string label = collector.IsRunning ? "수집 시작 중" : "수집 중지";
            if (collector.IsRunning && s != null)
                label = "수집 " + (int)(Json.Num(s, "active") ?? 0) + "개 진행 · 완료 " +
                    (int)(Json.Num(s, "completed") ?? 0) + " / " + (int)(Json.Num(s, "total") ?? 102);
            if (collectorError != null) label = "수집 시작 실패";
            var button = N<Button>("CollectorStatusButton");
            button.Content = label;
            button.ToolTip = collectorError ?? "클릭하여 102개 수집기 실행 상태 보기";
            N<Button>("CollectButton").Content = collector.IsRunning ? "수집 중지" : "수집 시작";
        }

        async void RefreshData()
        {
            if (closed) return;
            if (busy) { pending = true; return; }
            busy = true;
            pending = false;
            var localStore = store ?? (store = new DataStore(settings.DataRoot));
            N<Button>("RefreshButton").IsEnabled = false;
            Txt("ActivityText", "저장 파일 읽는 중…");
            try
            {
                var watch = Stopwatch.StartNew();
                Model result = await Task.Run(delegate
                {
                    DateTime utc = DateTime.UtcNow;
                    localStore.Refresh(utc);
                    Model m = new Engine(localStore).Run(utc);
                    m.RootMissing = !Directory.Exists(localStore.Root);
                    m.LoadMs = watch.Elapsed.TotalMilliseconds;
                    return m;
                });
                if (closed) return;
                model = result;
                lastDataPoll = DateTime.UtcNow;
                nextRun = result.NextUtc;
                RenderModel();
                Txt("ActivityText", "판정 " + Tz.Kst(result.DecidedUtc, "HH:mm:ss") + " · " + result.LoadMs.ToString("F0", CultureInfo.InvariantCulture) + " ms");
            }
            catch (Exception ex)
            {
                if (closed) return;
                model = null;
                if (setupWindow != null) setupWindow.UpdateData(null, store);
                nextRun = DateTime.UtcNow.AddMinutes(5);
                Txt("DecisionKicker", "현재 판정 · 관망", Amber);
                Txt("DecisionText", "관망 · 자료 처리 오류", Amber);
                Txt("DecisionDetail", ex.Message);
                Txt("StatusText", "현재 판정 미사용 · 다시 읽기 필요", Amber);
                Txt("ActivityText", "읽기 실패 · F5로 다시 시도", Amber);
            }
            finally
            {
                busy = false;
                if (!closed)
                {
                    N<Button>("RefreshButton").IsEnabled = true;
                    if (pending) RefreshData();
                }
            }
        }

        void Tick()
        {
            DateTime now = DateTime.UtcNow;
            UpdateCollectorStatus();
            var collection = collector.ReadState();
            double changed = collection == null ? -1 : Json.Num(collection, "changed") ?? -1;
            if (!busy && collector.IsRunning && changed >= 0 && changed != collectorChanged && (now - lastDataPoll).TotalSeconds >= 5)
            {
                collectorChanged = changed;
                RefreshData();
            }
            if (overlayKind == "collectors" && (now - lastOverlayRefresh).TotalSeconds >= 5)
                OpenOverlay("collectors");
            Txt("ClockText", Tz.Kst(now, "yyyy-MM-dd HH:mm:ss") + " KST");
            if (nextRun != DateTime.MaxValue)
            {
                TimeSpan wait = nextRun - now;
                Txt("DecisionNext", wait.TotalSeconds <= 0 ? (busy ? "계산 중" : "곧 갱신") :
                    Tz.Kst(nextRun, "HH:mm") + " · " + ((int)wait.TotalMinutes).ToString("00") + ":" + wait.Seconds.ToString("00"));
            }
            if (model != null)
            {
                var e = model.NextEvent;
                if (e != null && e.WhenUtc.HasValue)
                {
                    var left = e.WhenUtc.Value - now;
                    string remain = left.TotalSeconds >= 0 ?
                        (left.TotalHours >= 24 ? ((int)left.TotalDays) + "일 " : "") + ((int)left.TotalHours % 24) + "시간 " + left.Minutes + "분 후" :
                        "발표 후 " + (int)Math.Abs(left.TotalMinutes) + "분";
                    Txt("CountdownText", e.Name + " · " + remain);
                    N<TextBlock>("CountdownText").ToolTip = e.Name + " " + Tz.Kst(e.WhenUtc) + " KST";
                }
            }
            if (!busy && now >= nextRun) RefreshData();
        }

        void RenderModel()
        {
            Brush accent = model.IsBuy ? Green : Amber;
            Txt("DecisionKicker", model.Headline + " · " + Tz.Kst(model.DecidedUtc, "MM-dd HH:mm:ss") + " KST", accent);
            Txt("DecisionText", model.IsBuy ? model.Ticker + " · " + model.Reason : "관망 · " + model.Reason, accent);
            Txt("DecisionDetail", model.Detail);
            N<Border>("DecisionBorder").BorderBrush = accent;
            Txt("UsRegimeText", "미국 · " + model.UsRegime, model.UsState == 1 ? Green : Amber);
            Txt("CoinRegimeText", "코인 · " + model.CoinRegime, model.CoinState == 1 ? Green : Amber);
            Txt("UsRegimeNote", model.UsRegimeNote);
            Txt("CoinRegimeNote", model.CoinRegimeNote);
            Txt("CountdownText", model.NextEvent != null ? model.NextEvent.Name :
                (model.EventWarnings.Count > 0 ? model.EventWarnings[0] : "예정된 발표 없음"));
            Txt("SectorAxisText", model.SectorAxis);
            Txt("ThemeAxisText", model.ThemeAxis);
            Txt("StatusText", "기본 " + model.BaseOk + " / 70    옵션 " + model.OptOk + " / 32    전체 상태 보기");
            Txt("FolderText", (model.RootMissing ? "폴더 미수신 · " : "") + model.Root);
            int passCount = model.SetupCandidates.Count(c => c.Pass);
            N<Button>("RankingsButton").Content = "롱·숏 셋업 · 통과 " + passCount;
            if (setupWindow != null) setupWindow.UpdateData(model, store);
            var macro = N<Panel>("MacroHost");
            macro.Children.Clear();
            foreach (var chip in MainChips()) macro.Children.Add(ChipView(chip, true));
            DetailViews.FillEvents(N<Panel>("EventsHost"), model);
            DetailViews.FillNews(N<Panel>("NewsHost"), model);
            DetailViews.FillRisk(N<Panel>("RiskHost"), model);
            RenderSelection();
            if (overlayKind != null) OpenOverlay(overlayKind);
            Tick();
        }

        IEnumerable<Chip> MainChips()
        {
            foreach (string label in new[] { "금 GLD", "원유 USO", "구리 CPER", "달러 DXY", "미 2Y", "미 10Y", "VIX" })
            {
                var chip = model.Commod.Concat(model.Rates).FirstOrDefault(c => c.Label == label);
                if (chip != null) yield return chip;
            }
        }

        FrameworkElement ChipView(Chip chip, bool compact)
        {
            var lines = new StackPanel();
            lines.Children.Add(new TextBlock { Text = chip.Label, FontSize = compact ? 11 : 13, Foreground = Muted, TextTrimming = TextTrimming.CharacterEllipsis });
            lines.Children.Add(new TextBlock { Text = chip.Value ?? "미수신", FontFamily = AppFont.Family,
                FontSize = compact ? 17 : 24, Foreground = chip.Missing ? Muted : Ink, TextTrimming = TextTrimming.CharacterEllipsis });
            lines.Children.Add(new TextBlock { Text = chip.Delta ?? (chip.Missing ? "미수신" : "—"), FontSize = 11,
                Foreground = chip.Sign > 0 ? Green : chip.Sign < 0 ? Red : Muted, TextTrimming = TextTrimming.CharacterEllipsis });
            return new Border { Child = lines, Padding = new Thickness(compact ? 5 : 16, 0, compact ? 5 : 16, 0),
                ToolTip = chip.Label + " · " + chip.Value + " · " + chip.Delta + "\n원본 관측 " + (chip.Note ?? "미수신") };
        }

        void SelectGroup(string key, bool coin)
        {
            selectedGroup = key;
            selectedCoin = coin;
            selectedTicker = null;
            RenderSelection();
        }

        void SelectTicker(string ticker)
        {
            if (model == null) return;
            var c = model.Candidates.FirstOrDefault(x => x.Ticker == ticker);
            if (c == null) return;
            selectedTicker = ticker;
            selectedGroup = c.Group;
            selectedCoin = c.Market == "코인";
            RenderSelection();
        }

        void RenderSelection()
        {
            if (model == null) return;
            sectors.SetData(model.Sectors, !selectedCoin ? selectedGroup : null);
            themes.SetData(model.Themes, selectedCoin ? selectedGroup : null);
            FillGroups(N<Panel>("SectorList"), model.Sectors, false);
            FillGroups(N<Panel>("ThemeList"), model.Themes, true);
            string name = "판정 근거";
            if (selectedTicker != null) name = selectedTicker + " · 종목 상세";
            else if (selectedGroup != null) name = selectedGroup + " · " + (selectedCoin ? Catalog.ThemeName[selectedGroup] : Catalog.SectorName[selectedGroup]);
            Txt("DetailTitle", name);
            Txt("DetailSubtitle", selectedGroup != null ? "클릭으로 종목 상세 · Esc로 전체 판정" : "일정 → 시장 → 그룹 → 거래대금 → 옵션");
            DetailViews.Render(N<Panel>("ChecksHost"), N<Panel>("DetailHost"), model, selectedGroup, selectedCoin, selectedTicker, SelectTicker);
        }

        void OpenSetup()
        {
            if (setupWindow == null)
            {
                setupWindow = new SetupWindow { Owner = this };
                setupWindow.Closed += delegate { setupWindow = null; };
                setupWindow.Show();
            }
            if (model != null && store != null) setupWindow.UpdateData(model, store);
            if (setupWindow.WindowState == WindowState.Minimized) setupWindow.WindowState = WindowState.Normal;
            setupWindow.Activate();
        }

        void FillGroups(Panel host, List<SeriesLine> lines, bool coin)
        {
            host.Children.Clear();
            IEnumerable<SeriesLine> display = lines.Count > 0 ? lines.OrderBy(l => l.Rank > 0 ? l.Rank : 999) :
                (coin ? Catalog.Themes : Catalog.Sectors).Select(k => new SeriesLine { Key = k, Name = k + " " + (coin ? Catalog.ThemeName[k] : Catalog.SectorName[k]), Note = "미수신" });
            foreach (var line in display)
            {
                string key = line.Key;
                var row = new Grid();
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(21) });
                row.ColumnDefinitions.Add(new ColumnDefinition());
                row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                var rank = new TextBlock { Text = line.Rank > 0 ? line.Rank.ToString("00") : "—", Foreground = Muted, FontSize = 11, VerticalAlignment = VerticalAlignment.Center };
                var label = new TextBlock { Text = line.Name, Foreground = RelativeChart.SeriesBrush(key), FontSize = 12, TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
                var val = new TextBlock { Text = Engine.SPp(line.Last), Foreground = !line.Last.HasValue ? Muted : line.Last.Value >= 0 ? Green : Red,
                    FontFamily = AppFont.Family, FontSize = 12, Margin = new Thickness(5, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
                Grid.SetColumn(label, 1); Grid.SetColumn(val, 2);
                row.Children.Add(rank); row.Children.Add(label); row.Children.Add(val);
                var button = new Button { Content = row, HorizontalContentAlignment = HorizontalAlignment.Stretch, Padding = new Thickness(5, 2, 5, 2),
                    BorderThickness = new Thickness(0, 0, 0, 1), Margin = new Thickness(0, 0, 5, 0),
                    Background = selectedGroup == key && selectedCoin == coin ? BrushOf("#E5E7EB") : Brushes.Transparent,
                    BorderBrush = BrushOf("#DDDDDD"), ToolTip = line.Name + "\n초과수익 " + Engine.SPp(line.Last) + " · 1h " + Engine.SPp(line.Rs1h) +
                        "\n거래대금 " + Engine.Mult(line.VolMult) + (line.VolEst ? " (추정)" : "") + "\n" + line.Note };
                button.Click += delegate { SelectGroup(key, coin); };
                host.Children.Add(button);
            }
        }

        void OpenOverlay(string kind)
        {
            if (model == null && kind != "collectors") return;
            string query = "";
            bool restoreFocus = false;
            int caret = 0;
            var oldSearch = LogicalTreeHelper.FindLogicalNode(N<Grid>("OverlayBody"), kind == "collectors" ? "CollectorSearch" : "StatusSearch") as TextBox;
            if (oldSearch != null)
            {
                query = oldSearch.Text;
                restoreFocus = oldSearch.IsKeyboardFocusWithin;
                caret = oldSearch.CaretIndex;
            }
            FrameworkElement body;
            string title;
            switch (kind)
            {
                case "collectors": title = "102개 수집기 · 실행 상태"; body = CollectorViews.Build(collector.ReadState(), collector.IsRunning, collectorError); break;
                case "events": title = "글로벌 발표 일정 · KST"; body = DetailViews.Events(model); break;
                case "news": title = "지정학 · 코인 헤드라인"; body = DetailViews.News(model); break;
                case "risk": title = "옵션 위험 지표 · 원본 관측 기준"; body = DetailViews.Risks(model); break;
                case "macro":
                    title = "원자재 · 달러 · 금리 · 변동성";
                    var wrap = new WrapPanel();
                    foreach (var chip in model.Commod.Concat(model.Rates))
                    {
                        var item = ChipView(chip, false);
                        item.Width = 215; item.Height = 105;
                        wrap.Children.Add(item);
                    }
                    body = new ScrollViewer { Content = wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
                    break;
                default: kind = "status"; title = "전체 102개 수신 상태 · 원본 시각 KST"; body = DetailViews.Status(model); break;
            }
            overlayKind = kind;
            Txt("OverlayTitle", title);
            N<Grid>("OverlayBody").Children.Clear();
            N<Grid>("OverlayBody").Children.Add(body);
            N<Border>("Overlay").Visibility = Visibility.Visible;
            lastOverlayRefresh = DateTime.UtcNow;
            var newSearch = LogicalTreeHelper.FindLogicalNode(body, kind == "collectors" ? "CollectorSearch" : "StatusSearch") as TextBox;
            if (newSearch != null)
            {
                newSearch.Text = query;
                if (restoreFocus) { newSearch.Focus(); newSearch.CaretIndex = Math.Min(caret, query.Length); }
            }
        }

        void CloseOverlay()
        {
            overlayKind = null;
            N<Border>("Overlay").Visibility = Visibility.Collapsed;
            N<Grid>("OverlayBody").Children.Clear();
        }
    }
}
