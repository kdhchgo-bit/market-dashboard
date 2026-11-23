using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace MarketDash
{
    /// <summary>실제 OHLC와 계산된 셋업 가격만 표시하는 네이티브 차트.</summary>
    public class SetupChart : FrameworkElement
    {
        static readonly CultureInfo Culture = CultureInfo.InvariantCulture;
        static readonly Typeface Font = new Typeface(AppFont.Family, FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);
        static readonly Typeface BoldFont = new Typeface(AppFont.Family, FontStyles.Normal, FontWeights.SemiBold, FontStretches.Normal);
        static readonly Brush White = Brush("#FFFFFF"), Ink = Brush("#172631"), Muted = Brush("#76818C");
        static readonly Brush Up = Brush("#1F6F5C"), Down = Brush("#B23B3B"), Rule = Brush("#E8ECEF");
        static readonly Brush Entry = Brush("#176F70"), EntryFill = Brush("#DCECE7"), Last = Brush("#52687C");
        TradeSetup setup;
        List<Bar> shown = new List<Bar>();
        Rect plot;
        double candleStep;
        int hover = -1;
        readonly List<Level> drawnLevels = new List<Level>();
        double axisLow, axisHigh;
        bool axisLog;
        string dragKind;
        int dragIndex;
        double dragStartY, dragAnchorY, dragLow, dragHigh;
        bool dragLog;
        Rect dragPlot;
        Window dragWindow;

        public event Action<string, int, double> LevelChanged;

        sealed class Level
        {
            public double Price, AnchorY, BoxY;
            public string Caption;
            public Brush Color;
            public string Kind;
            public int Index = -1;
            public Rect Box;
        }

        public SetupChart()
        {
            SnapsToDevicePixels = true;
            ClipToBounds = true;
            Cursor = Cursors.Cross;
            MinHeight = 280;
            SizeChanged += delegate { EndDrag(); InvalidateVisual(); };
            Unloaded += delegate { EndDrag(); };
        }

        public void SetData(TradeSetup value)
        {
            if (!ReferenceEquals(setup, value)) EndDrag();
            setup = value;
            hover = -1;
            ToolTip = null;
            InvalidateVisual();
        }

        static Brush Brush(string hex)
        {
            var value = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
            value.Freeze();
            return value;
        }

        static bool Positive(double value)
        {
            return value > 0 && !double.IsInfinity(value) && !double.IsNaN(value);
        }

        static string Price(double value)
        {
            return value.ToString("#,0.00", Culture);
        }

        FormattedText Text(string value, double size, Brush color, bool bold = false)
        {
            return new FormattedText(value ?? "", Culture, FlowDirection.LeftToRight,
                bold ? BoldFont : Font, size, color, VisualTreeHelper.GetDpi(this).PixelsPerDip);
        }

        void Write(DrawingContext dc, string value, double x, double y, double size, Brush color, bool bold = false, double maxWidth = 0)
        {
            var text = Text(value, size, color, bold);
            if (maxWidth > 0)
            {
                text.MaxTextWidth = maxWidth;
                text.MaxLineCount = 1;
                text.Trimming = TextTrimming.CharacterEllipsis;
            }
            dc.DrawText(text, new Point(x, y));
        }

        protected override void OnRender(DrawingContext dc)
        {
            base.OnRender(dc);
            double width = ActualWidth, height = ActualHeight;
            shown.Clear();
            drawnLevels.Clear();
            plot = Rect.Empty;
            if (width <= 0 || height <= 0) return;
            dc.DrawRectangle(White, null, new Rect(0, 0, width, height));
            if (width < 280 || height < 160) return;
            if (setup == null)
            {
                Write(dc, "종목을 선택하면 셋업 차트가 표시됩니다.", 22, 28, 15, Muted);
                return;
            }
            shown = setup.Bars.Where(b => b != null && Positive(b.O) && Positive(b.H)
                && Positive(b.L) && Positive(b.C) && b.H >= b.L)
                .OrderBy(b => b.T).ToList();
            if (shown.Count > 180) shown = shown.Skip(shown.Count - 180).ToList();
            if (shown.Count == 0)
            {
                Write(dc, "완성 시세 봉 확인 대기", 22, 28, 17, Ink, true);
                Write(dc, setup.Reason, 22, 58, 13, Muted, false, width - 44);
                return;
            }

            string symbol = setup.Candidate == null ? "" : setup.Candidate.Ticker;
            string side = setup.IsShort ? "SHORT · 숏" : "LONG · 롱";
            Write(dc, symbol + " · " + side + " · " + setup.Minutes + "분봉", 14, 6, 16, setup.IsShort ? Down : Up, true);
            string stamp = "마지막 완성 봉 " + Tz.Kst(setup.AsOfUtc, "MM-dd HH:mm") + " KST · 진입·TP·SL 선 드래그";
            Write(dc, stamp, 14, 31, 11, Muted, false, width - 28);

            double axisWidth = width >= 650 ? 81 : 67;
            double boxWidth = width >= 800 ? 218 : width >= 500 ? 183 : 130;
            double right = axisWidth + boxWidth + 24;
            plot = new Rect(14, 64, Math.Max(60, width - right - 14), Math.Max(60, height - 106));
            var range = shown.SelectMany(b => new[] { b.L, b.H }).ToList();
            var levels = new List<Level>();
            if (setup.HasLevels)
            {
                if (Positive(setup.EntryLow) && Positive(setup.EntryHigh))
                {
                    range.Add(setup.EntryLow); range.Add(setup.EntryHigh);
                    levels.Add(new Level { Price = (setup.EntryLow + setup.EntryHigh) / 2, Kind = "entry",
                        Caption = "진입 " + Price(setup.EntryLow) + " – " + Price(setup.EntryHigh), Color = Entry });
                }
                if (Positive(setup.Stop))
                {
                    range.Add(setup.Stop);
                    levels.Add(new Level { Price = setup.Stop, Kind = "stop", Caption = "SL " + Price(setup.Stop), Color = Down });
                }
                for (int i = 0; i < setup.Targets.Length; i++)
                {
                    if (!Positive(setup.Targets[i])) continue;
                    range.Add(setup.Targets[i]);
                    string r = i < setup.R.Length && !double.IsNaN(setup.R[i]) && !double.IsInfinity(setup.R[i])
                        ? " · " + setup.R[i].ToString("0.00", Culture) + "R" : "";
                    levels.Add(new Level { Price = setup.Targets[i], Kind = "target", Index = i,
                        Caption = "TP" + (i + 1) + " " + Price(setup.Targets[i]) + r, Color = Up });
                }
            }
            double current = Positive(setup.Current) ? setup.Current : shown[shown.Count - 1].C;
            range.Add(current);
            levels.Add(new Level { Price = current, Caption = "현재 " + Price(current), Color = Last });
            double low = range.Min(), high = range.Max();
            double spread = Math.Max(high - low, current * 0.002);
            low = Math.Max(low - spread * 0.08, low * 0.85);
            high += spread * 0.08;
            bool log = high / low > 2.0;
            double yLow = log ? Math.Log(low) : low, yHigh = log ? Math.Log(high) : high;
            if (dragKind != null)
            {
                plot = dragPlot;
                log = dragLog; yLow = dragLow; yHigh = dragHigh;
            }
            axisLow = yLow; axisHigh = yHigh; axisLog = log;
            Func<double, double> y = price => plot.Bottom - ((log ? Math.Log(price) : price) - yLow) / (yHigh - yLow) * plot.Height;
            double axisX = width - axisWidth + 8;
            var gridPen = new Pen(Rule, 1);
            for (int i = 0; i <= 5; i++)
            {
                double v = yLow + (yHigh - yLow) * i / 5;
                double price = log ? Math.Exp(v) : v;
                double yy = y(price);
                dc.DrawLine(gridPen, new Point(plot.Left, yy), new Point(plot.Right, yy));
                Write(dc, Price(price), axisX, yy - 7, 10, Muted, false, axisWidth - 10);
            }
            dc.DrawLine(gridPen, new Point(width - axisWidth, plot.Top), new Point(width - axisWidth, plot.Bottom));

            if (setup.HasLevels && Positive(setup.EntryLow) && Positive(setup.EntryHigh))
            {
                double top = Math.Max(plot.Top, y(Math.Max(setup.EntryLow, setup.EntryHigh)));
                double bottom = Math.Min(plot.Bottom, y(Math.Min(setup.EntryLow, setup.EntryHigh)));
                if (bottom >= top) dc.DrawRectangle(EntryFill, null, new Rect(plot.Left, top, plot.Width, Math.Max(1, bottom - top)));
            }
            candleStep = plot.Width / (shown.Count + 8.0);
            double bodyWidth = Math.Max(1.2, Math.Min(10, candleStep * 0.62));
            dc.PushClip(new RectangleGeometry(plot));
            for (int i = 0; i < shown.Count; i++)
            {
                var b = shown[i];
                double x = plot.Left + candleStep * (i + 0.5);
                Brush color = b.C >= b.O ? Up : Down;
                dc.DrawLine(new Pen(color, 1), new Point(x, y(b.H)), new Point(x, y(b.L)));
                double top = y(Math.Max(b.O, b.C)), bottom = y(Math.Min(b.O, b.C));
                dc.DrawRectangle(color, null, new Rect(x - bodyWidth / 2, top, bodyWidth, Math.Max(1, bottom - top)));
            }
            dc.Pop();
            int ticks = Math.Max(2, Math.Min(5, (int)(plot.Width / 150)));
            for (int i = 0; i <= ticks; i++)
            {
                int index = (shown.Count - 1) * i / ticks;
                double x = plot.Left + candleStep * (index + 0.5);
                dc.DrawLine(gridPen, new Point(x, plot.Bottom), new Point(x, plot.Bottom + 5));
                var text = Text(Tz.Kst(shown[index].T, "MM-dd HH:mm"), 10, Muted);
                dc.DrawText(text, new Point(Math.Max(plot.Left, Math.Min(plot.Right - text.Width, x - text.Width / 2)), plot.Bottom + 9));
            }
            Write(dc, "KST" + (log ? " · 로그" : ""), axisX, plot.Bottom + 9, 10, Muted);
            if (hover >= 0 && hover < shown.Count)
            {
                var b = shown[hover];
                double x = plot.Left + candleStep * (hover + 0.5);
                dc.DrawLine(new Pen(Last, 0.8) { DashStyle = DashStyles.Dot }, new Point(x, plot.Top), new Point(x, plot.Bottom));
            }
            double lastX = plot.Left + candleStep * (shown.Count - 0.5);
            dc.DrawEllipse(Last, new Pen(White, 1), new Point(lastX, y(current)), 3.5, 3.5);
            DrawLevels(dc, levels, y, boxWidth, plot.Right + 10);
            if (!setup.HasLevels)
                Write(dc, setup.Reason ?? "셋업 가격 확인 대기", 20, plot.Top + 10, 12, Muted, false, plot.Width - 28);
        }

        void DrawLevels(DrawingContext dc, List<Level> levels, Func<double, double> y, double boxWidth, double boxX)
        {
            const double boxHeight = 25, spacing = 28;
            var ordered = levels.OrderByDescending(l => l.Price).ToList();
            for (int i = 0; i < ordered.Count; i++)
            {
                var level = ordered[i];
                level.AnchorY = y(level.Price);
                level.BoxY = Math.Max(plot.Top + boxHeight / 2, level.AnchorY);
                if (i > 0) level.BoxY = Math.Max(level.BoxY, ordered[i - 1].BoxY + spacing);
            }
            if (ordered.Count > 0)
            {
                ordered[ordered.Count - 1].BoxY = Math.Min(ordered[ordered.Count - 1].BoxY, plot.Bottom - boxHeight / 2);
                for (int i = ordered.Count - 2; i >= 0; i--)
                    ordered[i].BoxY = Math.Min(ordered[i].BoxY, ordered[i + 1].BoxY - spacing);
            }
            foreach (var level in ordered)
            {
                bool active = dragKind != null && level.Kind == dragKind && level.Index == dragIndex;
                var pen = new Pen(level.Color, active ? 2 : 1) { DashStyle = DashStyles.Dash };
                dc.DrawLine(pen, new Point(plot.Left, level.AnchorY), new Point(plot.Right, level.AnchorY));
                dc.DrawLine(new Pen(level.Color, 0.7), new Point(plot.Right, level.AnchorY), new Point(boxX, level.BoxY));
                var rect = new Rect(boxX, level.BoxY - boxHeight / 2, boxWidth, boxHeight);
                level.Box = rect;
                dc.DrawRoundedRectangle(White, new Pen(level.Color, 1), rect, 2, 2);
                if (level.Kind != null) dc.DrawEllipse(level.Color, new Pen(White, 1),
                    new Point(plot.Right - 4, level.AnchorY), active ? 4 : 3, active ? 4 : 3);
                var text = Text(level.Caption, 11, level.Color, true);
                text.MaxTextWidth = boxWidth - 14;
                text.MaxLineCount = 1;
                text.Trimming = TextTrimming.CharacterEllipsis;
                dc.DrawText(text, new Point(boxX + 7, level.BoxY - text.Height / 2));
            }
            drawnLevels.AddRange(ordered);
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            var point = e.GetPosition(this);
            if (dragKind != null)
            {
                if (e.LeftButton != MouseButtonState.Pressed)
                {
                    EndDrag();
                    return;
                }
                double yy = Math.Max(dragPlot.Top, Math.Min(dragPlot.Bottom, dragAnchorY + point.Y - dragStartY));
                double mapped = dragLow + (dragPlot.Bottom - yy) / dragPlot.Height * (dragHigh - dragLow);
                double price = dragLog ? Math.Exp(mapped) : mapped;
                if (Positive(price) && LevelChanged != null) LevelChanged(dragKind, dragIndex, price);
                e.Handled = true;
                InvalidateVisual();
                return;
            }
            Cursor = HitLevel(point) != null ? Cursors.SizeNS : Cursors.Cross;
            int index = plot.IsEmpty || candleStep <= 0 || !plot.Contains(point) ? -1
                : (int)((point.X - plot.Left) / candleStep);
            if (index >= shown.Count) index = -1;
            if (index == hover) return;
            hover = index;
            if (hover >= 0)
            {
                var b = shown[hover];
                ToolTip = Tz.Kst(b.T, "yyyy-MM-dd HH:mm") + " KST · " + setup.Minutes + "분봉"
                    + "\n시가 " + Price(b.O) + " · 고가 " + Price(b.H)
                    + "\n저가 " + Price(b.L) + " · 종가 " + Price(b.C);
            }
            else ToolTip = null;
            InvalidateVisual();
        }

        protected override void OnMouseLeave(MouseEventArgs e)
        {
            base.OnMouseLeave(e);
            if (dragKind != null) return;
            hover = -1;
            ToolTip = null;
            Cursor = Cursors.Cross;
            InvalidateVisual();
        }

        Level HitLevel(Point point)
        {
            var movable = drawnLevels.Where(l => l.Kind != null).ToList();
            var box = movable.FirstOrDefault(l => l.Box.Contains(point));
            if (box != null) return box;
            if (plot.IsEmpty || point.X < plot.Left || point.X > plot.Right) return null;
            return movable.Where(l => Math.Abs(l.AnchorY - point.Y) <= 6)
                .OrderBy(l => Math.Abs(l.AnchorY - point.Y)).FirstOrDefault();
        }

        protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonDown(e);
            var point = e.GetPosition(this);
            var level = HitLevel(point);
            if (level == null || plot.IsEmpty || plot.Height <= 0 || axisHigh <= axisLow) return;
            dragKind = level.Kind; dragIndex = level.Index;
            dragStartY = point.Y; dragAnchorY = level.AnchorY;
            dragPlot = plot; dragLow = axisLow; dragHigh = axisHigh; dragLog = axisLog;
            hover = -1; ToolTip = null; Cursor = Cursors.SizeNS;
            if (!CaptureMouse())
            {
                EndDrag();
                return;
            }
            dragWindow = Window.GetWindow(this);
            if (dragWindow != null) dragWindow.Deactivated += WindowDeactivated;
            e.Handled = true;
            InvalidateVisual();
        }

        protected override void OnMouseLeftButtonUp(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonUp(e);
            if (dragKind == null) return;
            e.Handled = true;
            EndDrag();
        }

        protected override void OnLostMouseCapture(MouseEventArgs e)
        {
            base.OnLostMouseCapture(e);
            EndDrag();
        }

        void WindowDeactivated(object sender, EventArgs e) { EndDrag(); }

        void EndDrag()
        {
            if (dragWindow != null) dragWindow.Deactivated -= WindowDeactivated;
            dragWindow = null;
            dragKind = null;
            if (IsMouseCaptured) ReleaseMouseCapture();
            Cursor = Cursors.Cross;
            InvalidateVisual();
        }
    }
}
