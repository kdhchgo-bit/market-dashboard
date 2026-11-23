using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;

namespace MarketDash
{
    public class SetupWindow : Window
    {
        readonly TextBlock listTitle, title, state, sourceTime, method, explanation;
        readonly ListBox candidates;
        readonly ComboBox direction;
        readonly SetupChart chart;
        readonly UniformGrid levels;
        readonly Dictionary<int, Button> timeButtons = new Dictionary<int, Button>();
        readonly Dictionary<string, SetupLevelEdit> manualLevels = new Dictionary<string, SetupLevelEdit>();
        readonly Button fullscreenButton, restoreButton;
        Model model;
        DataStore store;
        TradeSetup activeSetup;
        string editMessage;
        string selectedKey;
        int minutes = 15;
        bool rebuilding, fullscreen;
        static readonly CultureInfo Ci = CultureInfo.InvariantCulture;
        static readonly Brush Ink = Brush("#172124"), Muted = Brush("#65706C"), Rule = Brush("#D9E0DC");
        static readonly Brush Long = Brush("#1F6F5C"), Short = Brush("#B23B3B"), Surface = Brush("#F5F7F5");

        public SetupWindow()
        {
            Title = "롱 · 숏 셋업";
            FontFamily = AppFont.Family;
            Background = Brushes.White;
            Foreground = Ink;
            Width = 1440;
            Height = 840;
            MinWidth = 1050;
            MinHeight = 680;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;

            var root = new Grid { Margin = new Thickness(14, 10, 14, 12) };
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(42) });
            root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            Content = root;
            var toolbar = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 8) };
            var actions = new StackPanel { Orientation = Orientation.Horizontal };
            var back = Button("관측실로 돌아가기", delegate { Close(); });
            fullscreenButton = Button("창 모드 · F11", delegate { SetFullscreen(!fullscreen); });
            actions.Children.Add(fullscreenButton);
            actions.Children.Add(back);
            DockPanel.SetDock(actions, Dock.Right);
            toolbar.Children.Add(actions);
            toolbar.Children.Add(Text("롱 · 숏 셋업", 20, Ink, FontWeights.SemiBold));
            root.Children.Add(toolbar);

            var body = new Grid();
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(20, GridUnitType.Star) });
            body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(80, GridUnitType.Star) });
            Grid.SetRow(body, 1); root.Children.Add(body);
            var left = new Grid { Margin = new Thickness(0, 0, 12, 0) };
            left.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            left.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            left.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            listTitle = Text("종목 순위", 15, Ink, FontWeights.SemiBold);
            listTitle.Margin = new Thickness(0, 2, 0, 8);
            left.Children.Add(listTitle);
            direction = new ComboBox { ItemsSource = new[] { "전체 방향", "LONG · 롱", "SHORT · 숏" }, SelectedIndex = 0,
                FontSize = 13, Margin = new Thickness(0, 0, 0, 9), Padding = new Thickness(6, 3, 6, 3) };
            direction.SelectionChanged += delegate { RefreshCandidates(); };
            Grid.SetRow(direction, 1); left.Children.Add(direction);
            candidates = new ListBox { Background = Brushes.White, BorderBrush = Rule, BorderThickness = new Thickness(1),
                Padding = new Thickness(3), HorizontalContentAlignment = HorizontalAlignment.Stretch };
            ScrollViewer.SetHorizontalScrollBarVisibility(candidates, ScrollBarVisibility.Disabled);
            candidates.SelectionChanged += delegate
            {
                if (rebuilding) return;
                var item = candidates.SelectedItem as ListBoxItem;
                var candidate = item == null ? null : item.Tag as Candidate;
                selectedKey = candidate == null ? null : Key(candidate);
                RenderSelected();
            };
            Grid.SetRow(candidates, 2); left.Children.Add(candidates);
            body.Children.Add(left);

            var right = new Grid { Margin = new Thickness(9, 0, 0, 0) };
            right.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            right.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
            right.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            right.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            Grid.SetColumn(right, 1); body.Children.Add(right);
            var heading = new Grid { Margin = new Thickness(0, 0, 0, 8) };
            heading.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            heading.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var labels = new StackPanel();
            title = Text("종목을 불러오는 중", 24, Ink, FontWeights.SemiBold);
            state = Text("실제 저장 자료 기준", 14);
            state.Margin = new Thickness(0, 4, 0, 0);
            sourceTime = Text("", 11);
            sourceTime.Margin = new Thickness(0, 4, 0, 0);
            labels.Children.Add(title); labels.Children.Add(state); labels.Children.Add(sourceTime);
            heading.Children.Add(labels);
            var timeframes = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Top };
            foreach (int size in new[] { 5, 15, 60 })
            {
                int value = size;
                var button = Button(size == 60 ? "1시간" : size + "분", delegate { minutes = value; RenderSelected(); });
                timeButtons[size] = button;
                timeframes.Children.Add(button);
            }
            restoreButton = Button("자동값 복원", delegate
            {
                if (activeSetup != null && activeSetup.Candidate != null)
                    manualLevels.Remove(LevelKey(activeSetup.Candidate));
                editMessage = null;
                RenderSelected();
            });
            restoreButton.IsEnabled = false;
            timeframes.Children.Add(restoreButton);
            Grid.SetColumn(timeframes, 1); heading.Children.Add(timeframes);
            right.Children.Add(heading);
            chart = new SetupChart { ClipToBounds = true };
            chart.LevelChanged += MoveLevel;
            var frame = new Border { BorderBrush = Rule, BorderThickness = new Thickness(1), Background = Brushes.White, Child = chart };
            Grid.SetRow(frame, 1); right.Children.Add(frame);
            levels = new UniformGrid { Columns = 6, Margin = new Thickness(0, 10, 0, 0) };
            Grid.SetRow(levels, 2); right.Children.Add(levels);
            var notes = new StackPanel { Margin = new Thickness(0, 9, 0, 0) };
            method = Text("", 11);
            explanation = Text("", 12);
            method.TextWrapping = explanation.TextWrapping = TextWrapping.Wrap;
            notes.Children.Add(method); notes.Children.Add(explanation);
            Grid.SetRow(notes, 3); right.Children.Add(notes);

            Loaded += delegate { SetFullscreen(true); };
            KeyDown += delegate(object sender, KeyEventArgs e)
            {
                if (e.Key == System.Windows.Input.Key.F11) { SetFullscreen(!fullscreen); e.Handled = true; }
                else if (e.Key == System.Windows.Input.Key.Escape) { Close(); e.Handled = true; }
            };
        }

        public void UpdateData(Model value, DataStore data)
        {
            model = value;
            store = data;
            RefreshCandidates();
        }

        void RefreshCandidates()
        {
            if (candidates == null) return;
            if (model == null)
            {
                rebuilding = true;
                candidates.Items.Clear();
                selectedKey = null;
                rebuilding = false;
                listTitle.Text = "종목 순위 · 확인 대기";
                RenderSelected();
                return;
            }
            var source = model.SetupCandidates ?? new List<Candidate>();
            var ranked = source.Where(c => c.Pass).ToList();
            bool watchers = ranked.Count == 0;
            if (watchers) ranked = source.Where(c => c.Stage >= 3).ToList();
            int side = direction.SelectedIndex;
            var shown = ranked.Where(c => side == 0 || (side == 2) == c.IsShort).ToList();
            listTitle.Text = (watchers ? "관찰 순위" : "통과 순위") + " · " + shown.Count + "종목";
            rebuilding = true;
            candidates.Items.Clear();
            ListBoxItem selected = null;
            foreach (var candidate in shown)
            {
                int rank = ranked.IndexOf(candidate) + 1;
                var row = new StackPanel { Margin = new Thickness(3, 4, 3, 5) };
                var identity = new DockPanel();
                var badge = Text(candidate.Side, 11, candidate.IsShort ? Short : Long, FontWeights.SemiBold);
                badge.Margin = new Thickness(8, 0, 0, 0);
                DockPanel.SetDock(badge, Dock.Right); identity.Children.Add(badge);
                identity.Children.Add(Text(rank.ToString("00", Ci) + "  " + candidate.Ticker, 15, Ink, FontWeights.SemiBold));
                row.Children.Add(identity);
                row.Children.Add(Text(candidate.Name + " · " + candidate.Market, 11));
                var metrics = Text("거래대금 " + Multiple(candidate.VolMult) + " · 초과 " + PercentPoint(candidate.Excess), 11);
                metrics.Margin = new Thickness(0, 4, 0, 0); row.Children.Add(metrics);
                if (watchers) row.Children.Add(Text("보류 · " + candidate.Reason, 11));
                var item = new ListBoxItem { Content = row, Tag = candidate, Padding = new Thickness(4, 3, 4, 3),
                    HorizontalContentAlignment = HorizontalAlignment.Stretch, ToolTip = candidate.Reason };
                candidates.Items.Add(item);
                if (Key(candidate) == selectedKey) selected = item;
            }
            if (selected == null && candidates.Items.Count > 0) selected = (ListBoxItem)candidates.Items[0];
            candidates.SelectedItem = selected;
            var chosen = selected == null ? null : selected.Tag as Candidate;
            selectedKey = chosen == null ? null : Key(chosen);
            rebuilding = false;
            RenderSelected();
        }

        void RenderSelected()
        {
            foreach (var pair in timeButtons)
                pair.Value.Background = pair.Key == minutes ? Brush("#E5EEE8") : Surface;
            var item = candidates.SelectedItem as ListBoxItem;
            var candidate = item == null ? null : item.Tag as Candidate;
            levels.Children.Clear();
            editMessage = null;
            if (candidate == null || model == null || store == null)
            {
                activeSetup = null;
                restoreButton.IsEnabled = false;
                title.Text = model == null ? (store == null ? "저장 자료 확인 중" : "자료 처리 오류 · 다음 갱신 대기")
                    : "현재 해당 방향의 통과 종목 없음";
                title.Foreground = Ink;
                state.Text = model == null ? "다음 데이터 판정을 기다립니다." : model.Reason;
                state.Foreground = Muted;
                sourceTime.Text = model == null ? "" : "판정 " + Tz.Kst(model.DecidedUtc, "MM-dd HH:mm:ss") + " KST";
                method.Text = "";
                explanation.Text = "다음 데이터 갱신 시 통과 순위를 자동으로 반영합니다.";
                chart.SetData(null);
                return;
            }
            var data = store[candidate.Ticker];
            var bars = data == null ? new List<Bar>() : data.Bars;
            activeSetup = SetupBuilder.Build(candidate, bars, minutes, model.DecidedUtc);
            SetupLevelEdit saved;
            if (manualLevels.TryGetValue(LevelKey(candidate), out saved))
            {
                string reason;
                if (!SetupBuilder.ApplyLevels(activeSetup, saved, out reason))
                    editMessage = "수동값 적용 보류 · " + reason;
            }
            RenderPlan();
        }

        void MoveLevel(string kind, int index, double price)
        {
            if (activeSetup == null || activeSetup.Candidate == null) return;
            string reason;
            if (SetupBuilder.TryMoveLevel(activeSetup, kind, index, price, out reason))
            {
                manualLevels[LevelKey(activeSetup.Candidate)] = SetupLevelEdit.From(activeSetup);
                editMessage = null;
            }
            else editMessage = "조정 보류 · " + reason;
            RenderPlan();
        }

        void RenderPlan()
        {
            var setup = activeSetup;
            var candidate = setup.Candidate;
            levels.Children.Clear();
            var color = candidate.IsShort ? Short : Long;
            title.Text = candidate.Ticker + " · " + candidate.Name + "  " + candidate.Side;
            title.Foreground = color;
            state.Text = (setup.IsManual ? "수동 조정 · " : "") + setup.Status
                + (setup.Current > 0 ? " · 현재 " + Price(setup.Current) : "");
            state.Foreground = setup.CanEnter ? color : Muted;
            sourceTime.Text = "판정 " + Tz.Kst(model.DecidedUtc, "MM-dd HH:mm:ss") + " KST · "
                + (setup.AsOfUtc == DateTime.MinValue ? "차트 이력 확인 대기" : "완성 " + minutes + "분봉 " + Tz.Kst(setup.AsOfUtc, "MM-dd HH:mm") + " KST")
                + (setup.QuoteUtc == DateTime.MinValue ? "" : " · 현재가 봉 " + Tz.Kst(setup.QuoteUtc, "HH:mm") + " KST")
                + (candidate.Market == "코인" ? " · 현물 봉 기준" : "");
            chart.SetData(setup);
            restoreButton.IsEnabled = setup.IsManual || manualLevels.ContainsKey(LevelKey(candidate));
            AddLevel("진입 구간", setup.HasLevels ? Price(setup.EntryLow) + " – " + Price(setup.EntryHigh) : "확인 대기",
                setup.HasLevels ? "계획 평단 " + Price(setup.Entry) : setup.Reason, color);
            for (int i = 0; i < 3; i++)
            {
                bool available = setup.HasLevels && i < setup.Targets.Length;
                AddLevel("TP" + (i + 1), available ? Price(setup.Targets[i]) : "추가 매물대 없음",
                    available && i < setup.R.Length ? "비용 전 " + setup.R[i].ToString("0.00", Ci) + "R" : "", color);
            }
            AddLevel("SL · 무효 가격", setup.HasLevels ? Price(setup.Stop) : "확인 대기",
                setup.HasLevels ? "평단 대비 " + setup.RiskPct.ToString("0.00", Ci) + "%" : "", Short);
            AddLevel("판정", candidate.Pass ? "필터 통과" : "보류", candidate.Market + " · " + candidate.GroupName, Ink);
            method.Text = (setup.Method ?? "") + (string.IsNullOrWhiteSpace(setup.WaveNote) ? "" : " · " + setup.WaveNote);
            explanation.Text = (string.IsNullOrEmpty(editMessage) ? "" : editMessage + "\n")
                + (setup.Reason ?? "") + "\n" + candidate.Reason;
        }

        void AddLevel(string label, string value, string note, Brush color)
        {
            var content = new StackPanel();
            content.Children.Add(Text(label, 11));
            var price = Text(value, 15, color, FontWeights.SemiBold);
            price.Margin = new Thickness(0, 4, 0, 3);
            content.Children.Add(price);
            content.Children.Add(Text(note, 10));
            levels.Children.Add(new Border { Background = Surface, BorderBrush = Rule, BorderThickness = new Thickness(0, 1, 0, 0),
                Padding = new Thickness(9, 7, 7, 7), Margin = new Thickness(0, 0, 5, 0), Child = content, ToolTip = label + " · " + value + "\n" + note });
        }

        void SetFullscreen(bool value)
        {
            fullscreen = value;
            WindowState = WindowState.Normal;
            WindowStyle = value ? WindowStyle.None : WindowStyle.SingleBorderWindow;
            ResizeMode = value ? ResizeMode.NoResize : ResizeMode.CanResize;
            if (value) WindowState = WindowState.Maximized;
            fullscreenButton.Content = value ? "창 모드 · F11" : "전체화면 · F11";
        }

        static string Key(Candidate candidate) { return candidate.Ticker + "|" + candidate.Side; }
        string LevelKey(Candidate candidate) { return Key(candidate) + "|" + minutes.ToString(Ci); }
        static string Price(double value) { return value.ToString("#,0.00", Ci); }
        static string Multiple(double? value) { return value.HasValue ? value.Value.ToString("0.00", Ci) + "배" : "확인 대기"; }
        static string PercentPoint(double? value) { return value.HasValue ? (value.Value * 100).ToString("+0.00;-0.00;0.00", Ci) + "%p" : "확인 대기"; }
        static Brush Brush(string value) { var brush = (SolidColorBrush)new BrushConverter().ConvertFromString(value); brush.Freeze(); return brush; }
        static TextBlock Text(string value, double size, Brush color = null, FontWeight? weight = null)
        {
            return new TextBlock { Text = value ?? "", FontSize = size, Foreground = color ?? Muted, FontWeight = weight ?? FontWeights.Normal,
                TextTrimming = TextTrimming.CharacterEllipsis, VerticalAlignment = VerticalAlignment.Center };
        }
        static Button Button(string label, RoutedEventHandler click)
        {
            var button = new Button { Content = label, Padding = new Thickness(10, 5, 10, 5), FontSize = 12, Background = Surface, BorderBrush = Rule,
                BorderThickness = new Thickness(1), Foreground = Ink, Margin = new Thickness(5, 0, 0, 0), Cursor = Cursors.Hand };
            button.Click += click;
            return button;
        }
    }
}
