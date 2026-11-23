using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace MarketDash
{
    /// <summary>공통 완성봉의 초과수익률(%p)을 표시하는 서버 없는 WPF 차트.</summary>
    public class RelativeChart : FrameworkElement
    {
        static readonly CultureInfo Culture = CultureInfo.InvariantCulture;
        static readonly Typeface ContentTypeface = new Typeface(AppFont.Family, FontStyles.Normal, FontWeights.Normal, FontStretches.Normal);
        static readonly Brush Back = MakeBrush("#FFFFFF");
        static readonly Brush GridBrush = MakeBrush("#E3E5E7");
        static readonly Brush ZeroBrush = MakeBrush("#8C959F");
        static readonly Brush Muted = MakeBrush("#636363");
        static readonly Brush Ink = MakeBrush("#141414");
        static readonly string[] Palette = {
            "#007C83", "#9C5F00", "#315FC4", "#8051A3", "#387F4B", "#B64357",
            "#1D7B9A", "#82731D", "#776350", "#9B4886", "#437C70"
        };
        static readonly string[] Keys = {
            "XLK", "XLE", "XLF", "XLV", "XLI", "XLU", "XLP", "XLY", "XLB", "XLRE", "XLC",
            "L1", "DeFi", "L2", "XRP", "DOGE", "LINK"
        };
        static readonly Dictionary<string, Brush> Colors = BuildColors();

        sealed class Trace
        {
            public SeriesLine Line;
            public List<Point> Points = new List<Point>();
            public List<DateTime> Times = new List<DateTime>();
            public List<double> Values = new List<double>();
            public Rect Label;
        }
        sealed class EndLabel
        {
            public Trace Trace;
            public double Y;
        }

        List<SeriesLine> lines = new List<SeriesLine>();
        readonly List<Trace> traces = new List<Trace>();
        string selected;
        string hoverText;

        public event Action<string> Selected;

        public RelativeChart()
        {
            SnapsToDevicePixels = true;
            ClipToBounds = true;
            Cursor = Cursors.Hand;
            MinHeight = 90;
            ToolTip = "선을 누르면 구성 종목과 판정 근거를 표시합니다.";
        }

        public void SetData(List<SeriesLine> data, string selectedKey)
        {
            lines = data ?? new List<SeriesLine>();
            selected = selectedKey;
            traces.Clear();
            InvalidateVisual();
        }

        public static Brush SeriesBrush(string key)
        {
            Brush value;
            return key != null && Colors.TryGetValue(key, out value) ? value : Colors[Keys[0]];
        }

        static Brush MakeBrush(string hex)
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
            brush.Freeze();
            return brush;
        }

        static Dictionary<string, Brush> BuildColors()
        {
            var result = new Dictionary<string, Brush>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < Keys.Length; i++) result[Keys[i]] = MakeBrush(Palette[i < 11 ? i : i - 11]);
            return result;
        }

        static bool Finite(double v) { return !double.IsNaN(v) && !double.IsInfinity(v); }

        FormattedText Text(string text, double size, Brush brush, bool korean)
        {
            return new FormattedText(text ?? "", Culture, FlowDirection.LeftToRight,
                ContentTypeface, size, brush, VisualTreeHelper.GetDpi(this).PixelsPerDip);
        }

        void DrawText(DrawingContext dc, string text, Point at, double size, Brush brush, bool korean)
        {
            dc.DrawText(Text(text, size, brush, korean), at);
        }

        static string Signed(double value)
        {
            if (Math.Abs(value) < 0.0005) return "0.00";
            return value.ToString("+0.00;-0.00;0.00", Culture);
        }

        protected override void OnRender(DrawingContext dc)
        {
            base.OnRender(dc);
            traces.Clear();
            double width = ActualWidth, height = ActualHeight;
            if (width <= 0 || height <= 0) return;
            dc.DrawRectangle(Back, null, new Rect(0, 0, width, height));
            if (width < 170 || height < 75) return;

            var available = lines.Where(l => l != null && l.Pts != null && l.Pts.Any(p => Finite(p.Value))).ToList();
            if (available.Count == 0)
            {
                DrawText(dc, "미수신 · 이력 부족", new Point(16, Math.Max(12, height / 2 - 22)), 14, Ink, true);
                DrawText(dc, "기준시장과 그룹의 공통 5분봉 대기", new Point(16, Math.Max(34, height / 2 + 3)), 11, Muted, true);
                return;
            }

            double right = width >= 360 ? 110 : 74;
            Rect plot = new Rect(43, 14, Math.Max(20, width - 43 - right), Math.Max(25, height - 43));
            var points = available.SelectMany(l => l.Pts).Where(p => Finite(p.Value)).ToList();
            DateTime first = points.Min(p => p.Key), last = points.Max(p => p.Key);
            if (first == last) { first = first.AddMinutes(-5); last = last.AddMinutes(5); }
            double span = (last - first).TotalSeconds;
            double max = Math.Max(0.1, points.Max(p => Math.Abs(p.Value)));
            double step = NiceStep(max / 2.0);
            double range = Math.Ceiling(max * 1.08 / step) * step;
            if (!Finite(range) || range <= 0) range = 0.2;
            double zeroY = plot.Top + plot.Height / 2;

            dc.PushClip(new RectangleGeometry(new Rect(0, 0, width, height)));
            var gridPen = new Pen(GridBrush, 1);
            for (int i = -2; i <= 2; i++)
            {
                double value = range * i / 2;
                double y = zeroY - value / range * plot.Height / 2;
                dc.DrawLine(i == 0 ? new Pen(ZeroBrush, 1) : gridPen, new Point(plot.Left, y), new Point(plot.Right, y));
                string label = Math.Abs(value) < 0.000001 ? "0" : value.ToString("+0.##;-0.##", Culture);
                FormattedText axis = Text(label, 10, i == 0 ? Ink : Muted, false);
                dc.DrawText(axis, new Point(Math.Max(1, plot.Left - axis.Width - 7), y - axis.Height / 2));
            }
            int timeTicks = plot.Width < 220 ? 2 : 3;
            for (int i = 0; i <= timeTicks; i++)
            {
                double x = plot.Left + plot.Width * i / timeTicks;
                dc.DrawLine(gridPen, new Point(x, plot.Top), new Point(x, plot.Bottom));
                DateTime when = first.AddSeconds(span * i / timeTicks);
                string label = Tz.ToKst(when).ToString("HH:mm", Culture);
                FormattedText axis = Text(label, 10, Muted, false);
                double tx = Math.Max(plot.Left, Math.Min(plot.Right - axis.Width, x - axis.Width / 2));
                dc.DrawText(axis, new Point(tx, plot.Bottom + 8));
            }
            DrawText(dc, "%p", new Point(5, 0), 10, Muted, false);
            DrawText(dc, "KST", new Point(plot.Right + 8, plot.Bottom + 8), 9, Muted, false);

            var drawOrder = available.OrderBy(l => l.Key == selected ? 1 : 0).ToList();
            dc.PushClip(new RectangleGeometry(new Rect(plot.Left - 2, plot.Top - 2, plot.Width + 4, plot.Height + 4)));
            foreach (var line in drawOrder)
            {
                var trace = new Trace { Line = line };
                foreach (var p in line.Pts)
                {
                    if (!Finite(p.Value)) continue;
                    trace.Points.Add(new Point(plot.Left + (p.Key - first).TotalSeconds / span * plot.Width,
                        zeroY - p.Value / range * plot.Height / 2));
                    trace.Times.Add(p.Key);
                    trace.Values.Add(p.Value);
                }
                if (trace.Points.Count == 0) continue;
                traces.Add(trace);
                bool highlight = String.IsNullOrEmpty(selected) || line.Key == selected;
                dc.PushOpacity(highlight ? 1.0 : 0.36);
                Brush color = SeriesBrush(line.Key);
                var pen = new Pen(color, line.Key == selected ? 2.4 : 1.4);
                if (trace.Points.Count > 1)
                {
                    var geometry = new StreamGeometry();
                    using (StreamGeometryContext ctx = geometry.Open())
                    {
                        ctx.BeginFigure(trace.Points[0], false, false);
                        double expected = 300;
                        if (trace.Times.Count > 1)
                        {
                            var gaps = new List<double>();
                            for (int i = 1; i < trace.Times.Count; i++)
                            {
                                double gap = (trace.Times[i] - trace.Times[i - 1]).TotalSeconds;
                                if (gap > 0) gaps.Add(gap);
                            }
                            if (gaps.Count > 0) { gaps.Sort(); expected = gaps[gaps.Count / 2]; }
                        }
                        for (int i = 1; i < trace.Points.Count; i++)
                        {
                            if ((trace.Times[i] - trace.Times[i - 1]).TotalSeconds > expected * 2.1)
                                ctx.BeginFigure(trace.Points[i], false, false);
                            else ctx.LineTo(trace.Points[i], true, false);
                        }
                    }
                    geometry.Freeze();
                    dc.DrawGeometry(null, pen, geometry);
                }
                Point endpoint = trace.Points[trace.Points.Count - 1];
                dc.DrawEllipse(color, null, endpoint, line.Key == selected ? 3.2 : 2.1, line.Key == selected ? 3.2 : 2.1);
                dc.Pop();
            }
            dc.Pop();

            DrawEndLabels(dc, plot, width);
            dc.Pop();
        }

        static double NiceStep(double raw)
        {
            double power = Math.Pow(10, Math.Floor(Math.Log10(Math.Max(raw, 0.000001))));
            double scaled = raw / power;
            return (scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10) * power;
        }

        void DrawEndLabels(DrawingContext dc, Rect plot, double width)
        {
            if (traces.Count == 0) return;
            int capacity = Math.Max(2, (int)(plot.Height / 18));
            var ordered = traces.OrderByDescending(t => t.Values[t.Values.Count - 1]).ToList();
            var chosen = new List<Trace>();
            Trace selectedTrace = traces.FirstOrDefault(t => t.Line.Key == selected);
            if (selectedTrace != null) chosen.Add(selectedTrace);
            if (!chosen.Contains(ordered[0])) chosen.Add(ordered[0]);
            if (!chosen.Contains(ordered[ordered.Count - 1])) chosen.Add(ordered[ordered.Count - 1]);
            if (traces.Count <= capacity)
            {
                foreach (var remainingTrace in ordered)
                    if (!chosen.Contains(remainingTrace)) chosen.Add(remainingTrace);
            }
            else
            {
                foreach (var edgeTrace in ordered.Where((t, i) => i < 2 || i >= ordered.Count - 2))
                    if (chosen.Count < capacity && !chosen.Contains(edgeTrace)) chosen.Add(edgeTrace);
            }

            var labels = chosen.Select(t => new EndLabel { Trace = t, Y = t.Points[t.Points.Count - 1].Y })
                .OrderBy(l => l.Y).ToList();
            double spacing = Math.Min(18, plot.Height / Math.Max(1, labels.Count));
            for (int i = 0; i < labels.Count; i++)
            {
                double lower = plot.Top + i * spacing;
                labels[i].Y = Math.Max(lower, Math.Min(plot.Bottom, labels[i].Y));
                if (i > 0) labels[i].Y = Math.Max(labels[i].Y, labels[i - 1].Y + spacing);
            }
            for (int i = labels.Count - 1; i >= 0; i--)
            {
                labels[i].Y = Math.Min(labels[i].Y, plot.Bottom - (labels.Count - 1 - i) * spacing);
                if (i < labels.Count - 1) labels[i].Y = Math.Min(labels[i].Y, labels[i + 1].Y - spacing);
            }
            foreach (var label in labels)
            {
                Trace trace = label.Trace;
                Brush brush = SeriesBrush(trace.Line.Key);
                Point end = trace.Points[trace.Points.Count - 1];
                dc.PushOpacity(String.IsNullOrEmpty(selected) || trace.Line.Key == selected ? 1 : 0.65);
                dc.DrawLine(new Pen(brush, 0.7), end, new Point(plot.Right + 5, label.Y));
                string text = trace.Line.Key + " " + Signed(trace.Values[trace.Values.Count - 1]);
                FormattedText formatted = Text(text, 10.5, brush, false);
                formatted.MaxTextWidth = Math.Max(20, width - plot.Right - 9);
                formatted.Trimming = TextTrimming.CharacterEllipsis;
                dc.DrawText(formatted, new Point(plot.Right + 8, label.Y - 7));
                trace.Label = new Rect(plot.Right + 5, label.Y - 9, Math.Max(1, width - plot.Right - 5), 18);
                dc.Pop();
            }
        }

        Trace Nearest(Point point, out int index)
        {
            Trace best = null;
            index = -1;
            double distance = 18;
            foreach (var trace in traces)
            {
                if (!trace.Label.IsEmpty && trace.Label.Width > 0 && trace.Label.Contains(point))
                {
                    index = trace.Points.Count - 1;
                    return trace;
                }
                for (int i = 0; i < trace.Points.Count; i++)
                {
                    Point p = trace.Points[i];
                    double d = (p - point).Length;
                    if (d < distance) { distance = d; best = trace; index = i; }
                }
            }
            return best;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            int index;
            Trace trace = Nearest(e.GetPosition(this), out index);
            string text = trace == null ? "선을 누르면 구성 종목과 판정 근거를 표시합니다."
                : trace.Line.Name + "\n" + Tz.Kst(trace.Times[index], "MM-dd HH:mm") + " KST  "
                    + Signed(trace.Values[index]) + " %p"
                    + (trace.Line.Rs1h.HasValue ? "\n1시간 초과수익률 " + Signed(trace.Line.Rs1h.Value) + " %p" : "")
                    + (trace.Line.VolMult.HasValue ? "\n거래대금 " + trace.Line.VolMult.Value.ToString("0.00", Culture) + "배"
                        + (trace.Line.VolEst ? " · 추정" : "") : "\n거래대금 " + (trace.Line.Note ?? "이력 부족"));
            if (text != hoverText) { hoverText = text; ToolTip = text; }
        }

        protected override void OnMouseLeftButtonDown(MouseButtonEventArgs e)
        {
            base.OnMouseLeftButtonDown(e);
            int index;
            Trace trace = Nearest(e.GetPosition(this), out index);
            if (trace == null) return;
            selected = trace.Line.Key;
            InvalidateVisual();
            Action<string> handler = Selected;
            if (handler != null) handler(selected);
            e.Handled = true;
        }
    }
}
