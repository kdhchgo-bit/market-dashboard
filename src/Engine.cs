using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;

namespace MarketDash
{
    // ======================= 화면 모델 =======================
    public class Chip { public string Label, Value, Delta, Note; public int Sign; public bool Missing; }

    public class SeriesLine
    {
        public string Key, Name;
        public List<KeyValuePair<DateTime, double>> Pts = new List<KeyValuePair<DateTime, double>>();
        public double? Last { get { return Pts.Count > 0 ? Pts[Pts.Count - 1].Value : (double?)null; } }
        public double? Rs1h, VolMult;
        public bool VolEst;
        public int Rank;
        public string Note;
    }

    public class Candidate
    {
        public string Ticker, Name, Market, Group, GroupName, Bench;
        public double? R1h, G1h, B1h, VolMult;
        public bool VolEst;
        public int VolSamples;
        public bool IsShort;
        public string Side { get { return IsShort ? "SHORT" : "LONG"; } }
        public string Chain, Relation, ChainNote;
        public double? Spread;
        public int Stage;           // 0 일정·지정학, 1 레짐, 2 상대강도, 3 거래대금, 4 옵션, 5 통과
        public string Reason;
        public bool Pass { get { return Stage >= 5; } }
        public double? Excess { get { return R1h.HasValue && G1h.HasValue ? R1h - G1h : null; } }
        public double? DirectionalExcess { get { return IsShort ? -Excess : Excess; } }
    }

    public class Check { public string Step, Item, Value; public int State; } // 1 통과, 0 보류, -1 확인 대기

    public class EventItem
    {
        public string Name, Region, Raw, SourceTag;
        public DateTime Date;           // 현지 날짜
        public DateTime? WhenUtc;       // 시각 확정 시
        public bool Today;
    }

    public class NewsItem { public string Src, Title, Link; public DateTime? Pub; public bool Flag; }
    public class RiskItem { public string Label, Value, Note; public int Sign; }

    public class StatusRow
    {
        public Source Src;
        public string State, LastObs, Reason, Provider;
        public int Valid, Total;
        public bool Ok;
    }

    public class Model
    {
        public bool IsBuy;
        public string Headline, Ticker, Reason, Detail;
        public Candidate Winner, Watch;
        public DateTime DecidedUtc, NextUtc;
        public List<Chip> Commod = new List<Chip>(), Rates = new List<Chip>();
        public string UsRegime, CoinRegime, UsRegimeNote, CoinRegimeNote;
        public int UsState, CoinState;
        public EventItem NextEvent;
        public List<SeriesLine> Sectors = new List<SeriesLine>(), Themes = new List<SeriesLine>();
        public string SectorAxis, ThemeAxis;
        public List<Candidate> Candidates = new List<Candidate>();
        public List<Candidate> SetupCandidates = new List<Candidate>();
        public List<Check> Checks = new List<Check>();
        public List<EventItem> Events = new List<EventItem>();
        public List<string> EventWarnings = new List<string>();
        public List<NewsItem> News = new List<NewsItem>();
        public List<RiskItem> Risk = new List<RiskItem>();
        public List<StatusRow> Status = new List<StatusRow>();
        public int BaseOk, OptOk;
        public string Root;
        public bool RootMissing;
        public double LoadMs;
    }

    // ======================= 시간대 =======================
    public static class Tz
    {
        static TimeZoneInfo Find(string id, int fallbackHours)
        {
            try { return TimeZoneInfo.FindSystemTimeZoneById(id); }
            catch { return TimeZoneInfo.CreateCustomTimeZone(id, TimeSpan.FromHours(fallbackHours), id, id); }
        }
        public static readonly TimeZoneInfo ET = Find("Eastern Standard Time", -5);
        public static readonly TimeZoneInfo KST = Find("Korea Standard Time", 9);
        public static readonly TimeZoneInfo CET = Find("W. Europe Standard Time", 1);
        public static DateTime ToEt(DateTime utc) { return TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(utc, DateTimeKind.Utc), ET); }
        public static DateTime ToKst(DateTime utc) { return TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(utc, DateTimeKind.Utc), KST); }
        public static DateTime FromLocal(DateTime local, TimeZoneInfo z)
        {
            var l = DateTime.SpecifyKind(local, DateTimeKind.Unspecified);
            if (z.IsInvalidTime(l)) l = l.AddHours(1);
            return TimeZoneInfo.ConvertTimeToUtc(l, z);
        }
        public static bool UsOpen(DateTime utc)
        {
            var e = ToEt(utc);
            if (e.DayOfWeek == DayOfWeek.Saturday || e.DayOfWeek == DayOfWeek.Sunday) return false;
            var t = e.TimeOfDay;
            return t >= new TimeSpan(9, 30, 0) && t < new TimeSpan(16, 0, 0);
        }
        public static string Kst(DateTime? utc, string fmt = "MM-dd HH:mm") { return utc.HasValue ? ToKst(utc.Value).ToString(fmt, CultureInfo.InvariantCulture) : "—"; }
    }

    // ======================= 판정 설정(초기값, 변경 가능) =======================
    public static class Cfg
    {
        public static readonly double SpreadMax = Number("SpreadMax", 0.15, 0.001, 1);
        public static readonly int BarStaleMin = Integer("BarStaleMin", 10, 1, 1440);
        public static readonly int UsChainMaxAgeMin = Integer("UsChainMaxAgeMin", 35, 1, 1440);
        public static readonly int CoinChainMaxAgeMin = Integer("CoinChainMaxAgeMin", 25, 1, 1440);
        public static readonly int VolLookback = Integer("VolLookback", 20, 1, 90);
        public static readonly int VolMinSamples = Integer("VolMinSamples", Math.Min(20, VolLookback), 1, VolLookback);
        public static readonly double VolMultMin = Number("VolMultMin", 1.0, 0.01, 1000);
        public static readonly int EventBeforeMin = Integer("EventBeforeMin", 30, 0, 1440);
        public static readonly int EventAfterMin = Integer("EventAfterMin", 15, 0, 1440);
        public static readonly int NewsWindowMin = Integer("NewsWindowMin", 60, 1, 1440);
        public static readonly double UsoPct = Number("UsoPct", 0.95, 0.01, 0.999);

        static double Number(string key, double fallback, double min, double max)
        {
            double value;
            try
            {
                string text = System.Configuration.ConfigurationManager.AppSettings[key];
                if (double.TryParse(text, NumberStyles.Float, CultureInfo.InvariantCulture, out value) &&
                    !double.IsNaN(value) && !double.IsInfinity(value) && value >= min && value <= max) return value;
            }
            catch (System.Configuration.ConfigurationErrorsException) { }
            return fallback;
        }

        static int Integer(string key, int fallback, int min, int max)
        {
            int value;
            try
            {
                string text = System.Configuration.ConfigurationManager.AppSettings[key];
                if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out value) && value >= min && value <= max) return value;
            }
            catch (System.Configuration.ConfigurationErrorsException) { }
            return fallback;
        }
        public static readonly string[] GeoWords =
        {
            "sanction","embargo","blockade","pipeline","refinery","oil field","oilfield","tanker","strait","hormuz","red sea",
            "opec","supply disruption","shutdown","attack","strike","airstrike","missile","drone","invasion","invade","troops",
            "war ","warfare","clash","shelling","bombing","militar","ceasefire","conflict","houthi","hezbollah","escalat"
        };
    }

    // ======================= 판정 엔진 =======================
    public class Engine
    {
        readonly DataStore S;
        DateTime now;
        readonly Dictionary<string, Dictionary<DateTime, Bar>> idx = new Dictionary<string, Dictionary<DateTime, Bar>>();
        readonly Model M = new Model();

        public Engine(DataStore s) { S = s; }

        Dictionary<DateTime, Bar> Ix(string tag)
        {
            Dictionary<DateTime, Bar> d;
            if (idx.TryGetValue(tag, out d)) return d;
            d = new Dictionary<DateTime, Bar>();
            var sd = S[tag];
            if (sd != null) foreach (var b in sd.Bars) d[b.T] = b;
            idx[tag] = d;
            return d;
        }
        List<Bar> Bars(string tag) { var sd = S[tag]; return sd == null ? new List<Bar>() : sd.Bars; }
        Bar At(string tag, DateTime t) { Bar b; return Ix(tag).TryGetValue(t, out b) ? b : null; }

        double? Ret1h(string tag, DateTime t)
        {
            var a = At(tag, t); var b = At(tag, t.AddMinutes(-60));
            if (a == null || b == null || b.C <= 0) return null;
            return a.C / b.C - 1.0;
        }

        static double Median(List<double> v)
        {
            if (v.Count == 0) return double.NaN;
            var s = v.OrderBy(x => x).ToList();
            int n = s.Count;
            return n % 2 == 1 ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2.0;
        }
        static double Percentile(List<double> v, double p)
        {
            var s = v.OrderBy(x => x).ToList();
            if (s.Count == 0) return double.NaN;
            double r = p * (s.Count - 1);
            int lo = (int)Math.Floor(r), hi = (int)Math.Ceiling(r);
            return s[lo] + (s[hi] - s[lo]) * (r - lo);
        }

        // 거래대금: 코인은 호가통화 거래대금, 주식은 거래량×VWAP(없으면 전 구간 거래량×종가 '추정')
        double? Notional(Bar b, bool coin, bool useVwap)
        {
            if (coin) return b.Quote;
            if (!b.V.HasValue) return null;
            return useVwap ? b.V * b.Vwap : b.V * b.C;
        }

        /// <summary>최근 5분 거래대금 ÷ 지난 20거래일(코인 20일) 동시간대 중앙값.</summary>
        double? VolMult(string tag, DateTime t, bool coin, out bool est, out int samples, out string why)
        {
            est = false; samples = 0; why = null;
            var cur = At(tag, t);
            if (cur == null) { why = "동시각 봉 없음"; return null; }
            var bars = Bars(tag);
            bool useVwap = !coin && bars.Count > 0 && bars.All(b => b.Vwap.HasValue && b.Vwap > 0);
            if (!coin && !useVwap) est = true;
            double? nc = Notional(cur, coin, useVwap);
            if (!nc.HasValue) { why = coin ? "호가통화 거래대금 미수신 · 이력 부족" : "거래량 미수신"; return null; }
            var hist = new List<double>();
            if (coin)
            {
                for (int d = 1; d <= Cfg.VolLookback; d++)
                {
                    var b = At(tag, t.AddDays(-d));
                    if (b == null) continue;
                    var n = Notional(b, true, false);
                    if (n.HasValue) hist.Add(n.Value);
                }
            }
            else
            {
                var et = Tz.ToEt(t);
                var tod = et.TimeOfDay;
                var day = et.Date;
                for (int d = 1; d <= Math.Max(45, Cfg.VolLookback * 2 + 10) && hist.Count < Cfg.VolLookback; d++)
                {
                    var pd = day.AddDays(-d);
                    if (pd.DayOfWeek == DayOfWeek.Saturday || pd.DayOfWeek == DayOfWeek.Sunday) continue;
                    var pu = Tz.FromLocal(pd + tod, Tz.ET);
                    var b = At(tag, pu);
                    if (b == null) continue;
                    var n = Notional(b, false, useVwap);
                    if (n.HasValue) hist.Add(n.Value);
                }
            }
            samples = hist.Count;
            if (samples < Cfg.VolMinSamples) { why = (coin ? "호가통화 거래대금 이력 부족 " : "거래대금 이력 부족 ") + samples + "/" + Cfg.VolMinSamples; return null; }
            double med = Median(hist);
            if (!(med > 0)) { why = "과거 거래대금 0"; return null; }
            return nc.Value / med;
        }

        // ---------------- 옵션 체인 검사 ----------------
        public class ChainResult
        {
            public bool Pass; public string Reason; public double? Spread, AtmIv, Spot; public int? AgeMin; public DateTime? Exp; public DateTime? Snap;
        }

        ChainResult CheckChain(string tag, double? spotHint, bool coin)
        {
            var r = new ChainResult();
            var sd = S[tag];
            if (sd == null || !sd.FolderExists) { r.Reason = tag + " 폴더 미수신"; return r; }
            if (sd.Opts.Count == 0) { r.Reason = tag + " 유효 계약 미수신" + (sd.LastError != null ? " (" + sd.LastError + ")" : ""); return r; }
            var snap = sd.Opts.Max(o => o.Snap);
            r.Snap = snap;
            if (snap > now) { r.Reason = tag + " 원본 스냅샷이 미래 시각 · 확인 대기"; return r; }
            r.AgeMin = (int)Math.Round((now - snap).TotalMinutes);
            int lim = coin ? Cfg.CoinChainMaxAgeMin : Cfg.UsChainMaxAgeMin;
            if ((now - snap).TotalMinutes > lim) { r.Reason = tag + " 지연 " + r.AgeMin + "분 > " + lim + "분"; return r; }
            var rows = sd.Opts.Where(o => o.Snap == snap).ToList();
            var etToday = Tz.ToEt(now).Date;
            Func<OptRow, bool> live = o =>
            {
                if (!o.Exp.HasValue) return false;
                var e = o.Exp.Value;
                if (coin) { if (e.TimeOfDay == TimeSpan.Zero) e = e.AddHours(8); return e > now; }
                return e.Date > etToday;                       // 당일·만료 계약 제외
            };
            var groups = rows.Where(live).GroupBy(o => o.Exp.Value.Date).OrderBy(g => g.Key).ToList();
            // 스팟: 체인에 기록된 기초자산가 → 가격 시계열 → 패리티
            var unders = rows.Where(o => o.Under.HasValue && o.Under > 0).Select(o => o.Under.Value).ToList();
            double? spot = unders.Count > 0 ? Median(unders) : spotHint;
            r.Spot = spot;
            if (groups.Count == 0) { r.Reason = tag + " 같은 스냅샷의 유효 만기 미수신"; return r; }
            var near = groups[0]; // 근월이 미달이어도 다음 만기로 우회하지 않는다.
            r.Exp = near.Key;
            var strikes = near.Select(o => o.Strike).Where(k => k > 0).Distinct().OrderBy(k => k).ToList();
            if (strikes.Count < 3) { r.Reason = tag + " 근월 " + near.Key.ToString("MM/dd") + " 등가격 3행사가 부족"; return r; }
            int ci;
            if (spot.HasValue) ci = strikes.Select((k, i) => new { k, i }).OrderBy(x => Math.Abs(x.k - spot.Value)).First().i;
            else ci = strikes.Select((k, i) => new { k, i, d = ParityGap(near, k) }).OrderBy(x => x.d).First().i;
            ci = Math.Max(1, Math.Min(strikes.Count - 2, ci));
            var pick = new[] { strikes[ci - 1], strikes[ci], strikes[ci + 1] };
            var quotes = new List<OptRow>();
            foreach (var k in pick)
            {
                var call = near.LastOrDefault(o => o.Strike == k && o.Call);
                var put = near.LastOrDefault(o => o.Strike == k && !o.Call);
                if (call == null || put == null) { r.Reason = tag + " 근월 등가격 3행사가 콜·풋 6호가 미수신"; return r; }
                quotes.Add(call); quotes.Add(put);
            }
            var ivs = quotes.Where(q => q.Strike == pick[1] && q.Iv.HasValue && q.Iv > 0).Select(q => q.Iv.Value).ToList();
            if (ivs.Count > 0) { double iv = ivs.Average(); r.AtmIv = iv < 3 ? iv * 100 : iv; }
            int bad = quotes.Count(q => !(q.Bid.HasValue && q.Ask.HasValue && q.Bid > 0 && q.Bid <= q.Ask));
            if (bad > 0) { r.Reason = tag + " 등가격 6호가 중 " + bad + "개 무효(0 또는 역전)"; return r; }
            var sp = quotes.Select(q => (q.Ask.Value - q.Bid.Value) / ((q.Ask.Value + q.Bid.Value) / 2.0)).ToList();
            r.Spread = Median(sp);
            if (r.Spread > Cfg.SpreadMax) { r.Reason = tag + " 스프레드 중앙값 " + Pct(r.Spread.Value, 1) + " > " + Pct(Cfg.SpreadMax, 1); return r; }
            r.Pass = true;
            r.Reason = tag + " " + r.Exp.Value.ToString("MM/dd") + " 만기 스프레드 " + Pct(r.Spread.Value, 1);
            return r;
        }

        static double ParityGap(IGrouping<DateTime, OptRow> g, double k)
        {
            var c = g.FirstOrDefault(o => o.Strike == k && o.Call); var p = g.FirstOrDefault(o => o.Strike == k && !o.Call);
            if (c == null || p == null || !c.Bid.HasValue || !c.Ask.HasValue || !p.Bid.HasValue || !p.Ask.HasValue) return double.MaxValue;
            return Math.Abs((c.Bid.Value + c.Ask.Value) - (p.Bid.Value + p.Ask.Value));
        }

        // ---------------- 포맷 ----------------
        public static string Pct(double v, int d = 2) { return (v * 100).ToString("F" + d, CultureInfo.InvariantCulture) + "%"; }
        public static string SPct(double? v, int d = 2)
        {
            if (!v.HasValue) return "—";
            double x = v.Value * 100;
            return (x > 0 ? "+" : x < 0 ? "−" : "±") + Math.Abs(x).ToString("F" + d, CultureInfo.InvariantCulture) + "%";
        }
        public static string SPp(double? v, int d = 2)
        {
            if (!v.HasValue) return "—";
            double x = v.Value;
            return (x > 0 ? "+" : x < 0 ? "−" : "±") + Math.Abs(x).ToString("F" + d, CultureInfo.InvariantCulture) + "%p";
        }
        public static string Mult(double? v) { return v.HasValue ? v.Value.ToString("F2", CultureInfo.InvariantCulture) + "×" : "—"; }
        static string F(double v, int d) { return v.ToString("F" + d, CultureInfo.InvariantCulture); }
        static string Num(double v)
        {
            double a = Math.Abs(v);
            int d = a >= 1000 ? 1 : a >= 100 ? 2 : a >= 1 ? 2 : 4;
            return v.ToString("N" + d, CultureInfo.InvariantCulture);
        }

        // =====================================================================
        public Model Run(DateTime nowUtc)
        {
            now = nowUtc;
            M.Root = S.Root;
            M.DecidedUtc = now;
            var nb = new DateTime(now.Year, now.Month, now.Day, now.Hour, now.Minute / 5 * 5, 0, DateTimeKind.Utc).AddMinutes(5);
            M.NextUtc = nb;

            BuildStatus();
            BuildTopBand();
            var events = BuildEvents();
            BuildNews();
            BuildRisk();

            // ---------- 0단계: 일정·지정학 ----------
            string globalBlock = null;
            var calOk = new[] { "BLS", "FOMC", "ECB" }.Where(t =>
                M.Status.Any(row => row.Src.Tag == t && row.Ok) &&
                events.Any(e => e.SourceTag == t && (e.WhenUtc.HasValue ? e.WhenUtc.Value > now :
                    e.Date > TimeZoneInfo.ConvertTimeFromUtc(now, t == "ECB" ? Tz.CET : Tz.ET).Date))).ToList();
            var calMissing = new[] { "BLS", "FOMC", "ECB" }.Where(t => !calOk.Contains(t)).ToList();
            if (calMissing.Count > 0)
            {
                globalBlock = "필수 일정 미수신·지연 또는 향후 일정 없음 (" + string.Join("·", calMissing) + ") — 판정 보류";
                M.Checks.Add(new Check { Step = "일정", Item = "필수 일정 수신", Value = string.Join("·", calMissing) + " 미수신·지연/향후 일정 없음", State = -1 });
            }
            else M.Checks.Add(new Check { Step = "일정", Item = "필수 일정 수신", Value = "BLS·FOMC·ECB", State = 1 });

            var hit = events.FirstOrDefault(e => e.WhenUtc.HasValue && now >= e.WhenUtc.Value.AddMinutes(-Cfg.EventBeforeMin) && now <= e.WhenUtc.Value.AddMinutes(Cfg.EventAfterMin));
            if (hit != null)
            {
                globalBlock = globalBlock ?? (hit.Name + " 발표 " + Tz.Kst(hit.WhenUtc, "HH:mm") + " KST 전후 " + Cfg.EventBeforeMin + "/" + Cfg.EventAfterMin + "분 — 두 시장 보류");
                M.Checks.Add(new Check { Step = "일정", Item = "발표 창(−" + Cfg.EventBeforeMin + "~+" + Cfg.EventAfterMin + "분)", Value = hit.Name + " " + Tz.Kst(hit.WhenUtc, "MM-dd HH:mm"), State = 0 });
            }
            else
            {
                var nx = events.FirstOrDefault(e => e.WhenUtc.HasValue && e.WhenUtc > now);
                M.Checks.Add(new Check { Step = "일정", Item = "발표 창(−" + Cfg.EventBeforeMin + "~+" + Cfg.EventAfterMin + "분)", Value = nx != null ? "다음 " + nx.Name + " " + Tz.Kst(nx.WhenUtc, "MM-dd HH:mm") : "해당 없음", State = 1 });
            }
            foreach (var w in M.EventWarnings) M.Checks.Add(new Check { Step = "일정", Item = "시각 미정 경고", Value = w, State = -1 });

            // 지정학 + USO 충격
            var geo = M.News.Where(n => n.Src == "BBC" && n.Flag && n.Pub.HasValue && n.Pub.Value <= now && (now - n.Pub.Value).TotalMinutes <= Cfg.NewsWindowMin).ToList();
            string usoNote; bool usoShock = UsoShock(out usoNote);
            if (geo.Count > 0 && usoShock)
            {
                globalBlock = globalBlock ?? ("지정학 기사 " + geo.Count + "건 + USO 충격 " + usoNote);
                M.Checks.Add(new Check { Step = "지정학", Item = "기사 × USO 충격", Value = geo.Count + "건 · " + usoNote, State = 0 });
            }
            else
                M.Checks.Add(new Check { Step = "지정학", Item = "기사 × USO 충격", Value = "기사 " + geo.Count + "건 · USO " + usoNote, State = 1 });

            // ---------- 1단계: 시장 레짐 ----------
            DateTime? tUs = null, tC = null;
            string usBlock = UsRegime(out tUs);
            string coinBlock = CoinRegime(out tC);
            M.UsState = usBlock == null ? 1 : 0;
            M.CoinState = coinBlock == null ? 1 : 0;

            // ---------- 후보 ----------
            foreach (var kv in Catalog.StockSector)
                M.Candidates.Add(EvalStock(kv.Key, kv.Value, tUs, globalBlock, usBlock));
            foreach (var a in Catalog.Alts)
                M.Candidates.Add(EvalCoin(a, tC, globalBlock, coinBlock));

            var shortUs = ShortRegime("SPY", tUs, usBlock);
            var shortCoin = ShortRegime("BTC", tC, coinBlock);
            M.SetupCandidates.AddRange(M.Candidates);
            foreach (var kv in Catalog.StockSector)
                M.SetupCandidates.Add(EvalStock(kv.Key, kv.Value, tUs, globalBlock, shortUs, true));
            foreach (var a in Catalog.Alts)
                M.SetupCandidates.Add(EvalCoin(a, tC, globalBlock, shortCoin, true));
            M.SetupCandidates = M.SetupCandidates.OrderByDescending(c => c.Stage)
                .ThenByDescending(c => c.VolMult ?? -1).ThenByDescending(c => c.DirectionalExcess ?? -9)
                .ThenBy(c => c.Ticker, StringComparer.Ordinal).ThenBy(c => c.Side, StringComparer.Ordinal).ToList();

            BuildCharts(tUs, tC);

            var pass = M.Candidates.Where(c => c.Pass)
                .OrderByDescending(c => c.VolMult).ThenByDescending(c => c.Excess).ThenBy(c => c.Ticker, StringComparer.Ordinal).ToList();
            M.Candidates = M.Candidates
                .OrderByDescending(c => c.Stage).ThenByDescending(c => c.VolMult ?? -1).ThenByDescending(c => c.Excess ?? -9).ThenBy(c => c.Ticker, StringComparer.Ordinal).ToList();
            if (pass.Count > 0)
            {
                var w = pass[0];
                M.IsBuy = true; M.Winner = w; M.Ticker = w.Ticker;
                M.Headline = "오늘의 매수 후보";
                M.Reason = w.Name + " · " + w.Market + " " + w.GroupName;
                M.Detail = "종목 1h " + SPct(w.R1h) + " > " + w.GroupName + " " + SPct(w.G1h) + " > " + w.Bench + " " + SPct(w.B1h)
                    + "  ·  그룹 대비 " + SPp(w.Excess * 100) + "  ·  거래대금 " + Mult(w.VolMult) + (w.VolEst ? "(추정)" : "")
                    + "  ·  " + w.Chain + " " + w.Relation + " 스프레드 " + (w.Spread.HasValue ? Pct(w.Spread.Value, 1) : "—")
                    + (pass.Count > 1 ? "  ·  통과 " + pass.Count + "종목 중 1위" : "");
            }
            else
            {
                M.IsBuy = false; M.Headline = "관망";
                var top = M.Candidates.FirstOrDefault();
                string reason = globalBlock;
                if (reason == null && top != null)
                {
                    reason = top.Reason;
                    if (top.Stage == 1)
                    {
                        // 두 시장 모두 레짐에서 막힘 → 장중 시장(코인) 사유를 우선 표시
                        reason = coinBlock != null && usBlock != null && !Tz.UsOpen(now) ? "코인 · " + coinBlock : (usBlock != null ? "미국 · " + usBlock : "코인 · " + coinBlock);
                    }
                    else reason = top.Ticker + " · " + reason;
                }
                M.Reason = reason ?? "판정 가능한 후보 없음";
                M.Detail = "재판정 " + Tz.Kst(M.NextUtc, "HH:mm") + " KST  ·  가장 멀리 진행한 단계: " + StageName(top != null ? top.Stage : 0);
                M.Watch = M.Candidates.FirstOrDefault(c => c.Stage == 4 || (c.Stage == 3 && !c.VolMult.HasValue));
                if (M.Watch != null)
                {
                    var w = M.Watch;
                    M.Reason = "관찰 1순위 " + w.Ticker + " · " + (w.Stage == 4 ? "옵션 보류" : "거래대금 확인 대기");
                    M.Detail = "그룹 대비 " + SPp(w.Excess * 100)
                        + (w.VolMult.HasValue ? " · 거래대금 " + Mult(w.VolMult) : "")
                        + " · " + w.Reason + " · 재판정 " + Tz.Kst(M.NextUtc, "HH:mm") + " KST";
                }
            }
            return M;
        }

        public static string StageName(int s)
        {
            switch (s) { case 0: return "일정·지정학"; case 1: return "시장 레짐"; case 2: return "그룹 상대강도"; case 3: return "거래대금"; case 4: return "옵션 호가"; default: return "통과"; }
        }

        // ---------------- 레짐 ----------------
        string UsRegime(out DateTime? t)
        {
            t = null;
            var spy = S["SPY"].Last;
            bool open = Tz.UsOpen(now);
            var et = Tz.ToEt(now);
            string block = null;
            if (!open)
            {
                block = "미국 정규장 아님 (ET " + et.ToString("ddd HH:mm", CultureInfo.InvariantCulture) + ")";
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "정규장", Value = "장외 · ET " + et.ToString("HH:mm"), State = 0 });
            }
            else if (spy == null)
            {
                block = "SPY 5분봉 미수신";
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "SPY 5분봉", Value = "미수신", State = -1 });
            }
            else if ((now - spy.End).TotalMinutes > Cfg.BarStaleMin)
            {
                block = "SPY 5분봉 지연 " + (int)(now - spy.End).TotalMinutes + "분";
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "SPY 5분봉", Value = "지연 " + (int)(now - spy.End).TotalMinutes + "분", State = -1 });
            }
            else
            {
                t = spy.T;
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "완성 5분봉", Value = Tz.Kst(spy.End, "HH:mm") + " KST", State = 1 });
                var r = Ret1h("SPY", spy.T);
                if (!r.HasValue) { block = "SPY 1시간 이력 부족"; M.Checks.Add(new Check { Step = "미국 레짐", Item = "SPY 1h", Value = "이력 부족", State = -1 }); }
                else
                {
                    M.Checks.Add(new Check { Step = "미국 레짐", Item = "SPY 1h > 0", Value = SPct(r), State = r > 0 ? 1 : 0 });
                    if (r <= 0) block = "SPY 1시간 " + SPct(r) + " ≤ 0";
                }
            }
            // VIX9D ≤ VIX3M (74, 같은 관측일)
            double? v9, v3; DateTime? od;
            VixTerm(out v9, out v3, out od);
            if (!v9.HasValue || !v3.HasValue)
            {
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "VIX9D ≤ VIX3M", Value = "확인 대기 (동일 관측일 값 없음)", State = -1 });
                if (block == null) block = "VIX 기간구조 확인 대기";
            }
            else
            {
                bool ok = v9 <= v3;
                M.Checks.Add(new Check { Step = "미국 레짐", Item = "VIX9D ≤ VIX3M", Value = F(v9.Value, 2) + " / " + F(v3.Value, 2) + " · " + od.Value.ToString("MM-dd"), State = ok ? 1 : 0 });
                if (!ok && block == null) block = "VIX9D " + F(v9.Value, 2) + " > VIX3M " + F(v3.Value, 2);
            }
            M.UsRegime = block == null ? "열림" : "닫힘";
            M.UsRegimeNote = block ?? ("SPY 1h " + SPct(t.HasValue ? Ret1h("SPY", t.Value) : null));
            return block;
        }

        string CoinRegime(out DateTime? t)
        {
            t = null;
            var btc = S["BTC"].Last;
            string block = null;
            if (btc == null) { block = "BTC 5분봉 미수신"; M.Checks.Add(new Check { Step = "코인 레짐", Item = "BTC 5분봉", Value = "미수신", State = -1 }); }
            else if ((now - btc.End).TotalMinutes > Cfg.BarStaleMin)
            {
                block = "BTC 5분봉 지연 " + (int)(now - btc.End).TotalMinutes + "분";
                M.Checks.Add(new Check { Step = "코인 레짐", Item = "BTC 5분봉", Value = "지연 " + (int)(now - btc.End).TotalMinutes + "분 · 마지막 " + Tz.Kst(btc.End, "MM-dd HH:mm"), State = -1 });
            }
            else
            {
                t = btc.T;
                var r = Ret1h("BTC", btc.T);
                M.Checks.Add(new Check { Step = "코인 레짐", Item = "완성 5분봉", Value = Tz.Kst(btc.End, "HH:mm") + " KST", State = 1 });
                if (!r.HasValue) { block = "BTC 1시간 이력 부족"; M.Checks.Add(new Check { Step = "코인 레짐", Item = "BTC 1h", Value = "이력 부족", State = -1 }); }
                else
                {
                    M.Checks.Add(new Check { Step = "코인 레짐", Item = "BTC 1h > 0", Value = SPct(r), State = r > 0 ? 1 : 0 });
                    if (r <= 0) block = "BTC 1시간 " + SPct(r) + " ≤ 0";
                }
            }
            M.CoinRegime = block == null ? "열림" : "닫힘";
            M.CoinRegimeNote = block ?? ("BTC 1h " + SPct(t.HasValue ? Ret1h("BTC", t.Value) : null));
            return block;
        }

        void VixTerm(out double? v9, out double? v3, out DateTime? obsDate)
        {
            v9 = v3 = null; obsDate = null;
            var sd = S["VIX_TERM"];
            if (sd == null || !M.Status.Any(row => row.Src.Tag == "VIX_TERM" && row.Ok)) return;
            var last = new Dictionary<string, KeyValuePair<DateTime, double>>();
            foreach (var r in sd.Rows)
            {
                string sym = (Json.Str(r, "symbol") ?? "").Replace("^", "").ToUpperInvariant();
                var v = Json.Num(r, "value", "close", "c");
                var t = Json.Time(r, "obs_time", "obs_date", "date");
                if (sym.Length == 0 || !v.HasValue || !(v.Value > 0) || !t.HasValue || t.Value > now || (now - t.Value).TotalDays > 5) continue;
                KeyValuePair<DateTime, double> cur;
                if (!last.TryGetValue(sym, out cur) || t.Value >= cur.Key) last[sym] = new KeyValuePair<DateTime, double>(t.Value, v.Value);
            }
            KeyValuePair<DateTime, double> a, b;
            if (last.TryGetValue("VIX9D", out a) && last.TryGetValue("VIX3M", out b) && Tz.ToEt(a.Key).Date == Tz.ToEt(b.Key).Date)
            {
                v9 = a.Value; v3 = b.Value; obsDate = Tz.ToEt(a.Key).Date;
            }
        }

        bool UsoShock(out string note)
        {
            var uso = S["USO"].Last;
            if (uso == null) { note = "미수신"; return false; }
            if (!Tz.UsOpen(now) || (now - uso.End).TotalMinutes > Cfg.BarStaleMin) { note = "휴장·지연(제외)"; return false; }
            var r = Ret1h("USO", uso.T);
            if (!r.HasValue) { note = "1h 이력 부족"; return false; }
            var et = Tz.ToEt(uso.T);
            var hist = new List<double>();
            for (int d = 1; d <= Math.Max(45, Cfg.VolLookback * 2 + 10) && hist.Count < Cfg.VolLookback; d++)
            {
                var pd = et.Date.AddDays(-d);
                if (pd.DayOfWeek == DayOfWeek.Saturday || pd.DayOfWeek == DayOfWeek.Sunday) continue;
                var pr = Ret1h("USO", Tz.FromLocal(pd + et.TimeOfDay, Tz.ET));
                if (pr.HasValue) hist.Add(Math.Abs(pr.Value));
            }
            if (hist.Count < Cfg.VolMinSamples) { note = "1h " + SPct(r) + " (백분위 이력 부족)"; return false; }
            double p = Percentile(hist, Cfg.UsoPct);
            note = "|1h| " + Pct(Math.Abs(r.Value)) + " / " + F(Cfg.UsoPct * 100, 1) + "백분위 " + Pct(p);
            return Math.Abs(r.Value) > p;
        }

        // ---------------- 후보 평가 ----------------
        string Name(string t) { Source s; return Catalog.ByTag.TryGetValue(t, out s) ? s.Name : t; }

        string ShortRegime(string benchmark, DateTime? t, string unavailable)
        {
            if (!t.HasValue) return unavailable ?? benchmark + " 기준 봉 확인 대기";
            var r = Ret1h(benchmark, t.Value);
            if (!r.HasValue) return benchmark + " 1시간 이력 부족";
            if (r >= 0) return benchmark + " 1시간 " + SPct(r) + " ≥ 0 · 숏 레짐 대기";
            if (benchmark == "SPY")
            {
                double? v9, v3; DateTime? obs;
                VixTerm(out v9, out v3, out obs);
                if (!v9.HasValue || !v3.HasValue) return "VIX 기간구조 확인 대기";
            }
            return null;
        }

        Candidate EvalStock(string tk, string sec, DateTime? t, string g, string block, bool isShort = false)
        {
            var c = new Candidate { Ticker = tk, Name = Name(tk), Market = "미국", Group = sec, GroupName = Catalog.SectorName[sec], Bench = "SPY", IsShort = isShort };
            string ct, rel; Catalog.StockChain(tk, out ct, out rel); c.Chain = ct; c.Relation = rel;
            DateTime tt = t ?? (S["SPY"].Last != null ? S["SPY"].Last.T : DateTime.MinValue);
            if (tt != DateTime.MinValue) { c.R1h = Ret1h(tk, tt); c.G1h = Ret1h(sec, tt); c.B1h = Ret1h("SPY", tt); }
            bool est; int n; string why;
            if (tt != DateTime.MinValue) { c.VolMult = VolMult(tk, tt, false, out est, out n, out why); c.VolEst = est; c.VolSamples = n; }
            if (g != null) { c.Stage = 0; c.Reason = g; return c; }
            if (block != null) { c.Stage = 1; c.Reason = block; return c; }
            return Funnel(c, t.Value, false, tk, sec);
        }

        Candidate EvalCoin(string tk, DateTime? t, string g, string block, bool isShort = false)
        {
            string th = Catalog.CoinTheme(tk);
            var c = new Candidate { Ticker = tk, Name = Name(tk), Market = "코인", Group = th, GroupName = Catalog.ThemeName[th], Bench = "BTC", IsShort = isShort };
            string ct, rel; Catalog.CoinChain(tk, out ct, out rel); c.Chain = ct; c.Relation = rel;
            DateTime tt = t ?? (S["BTC"].Last != null ? S["BTC"].Last.T : DateTime.MinValue);
            if (tt != DateTime.MinValue)
            {
                c.R1h = Ret1h(tk, tt); c.B1h = Ret1h("BTC", tt); c.G1h = ThemeRet(tk, tt);
                if (Catalog.ThemeMembers[th].Length == 1) c.GroupName = "알트11 중앙값";
                bool est; int n; string why;
                c.VolMult = VolMult(tk, tt, true, out est, out n, out why); c.VolEst = est; c.VolSamples = n;
            }
            if (g != null) { c.Stage = 0; c.Reason = g; return c; }
            if (block != null) { c.Stage = 1; c.Reason = block; return c; }
            return Funnel(c, t.Value, true, tk, null);
        }

        double? ThemeRet(string coin, DateTime t)
        {
            string th = Catalog.CoinTheme(coin);
            var mem = Catalog.ThemeMembers[th].Length > 1 ? Catalog.ThemeMembers[th] : Catalog.Alts.Where(a => a != coin).ToArray();
            var v = mem.Select(m => Ret1h(m, t)).Where(x => x.HasValue).Select(x => x.Value).ToList();
            if (v.Count == 0 || v.Count < (mem.Length + 1) / 2) return null;
            return Median(v);
        }

        Candidate Funnel(Candidate c, DateTime t, bool coin, string tk, string sec)
        {
            // 2단계: 종목 > 그룹 > 기준시장 > 0
            if (!c.R1h.HasValue) { c.Stage = 2; c.Reason = At(tk, t) == null ? "동시각 완성봉 없음" : "1시간 이력 부족"; return c; }
            if (!c.G1h.HasValue) { c.Stage = 2; c.Reason = c.GroupName + " 1시간 수익률 미수신"; return c; }
            double side = c.IsShort ? -1 : 1;
            string comparison = c.IsShort ? " ≥ " : " ≤ ";
            if (!(side * c.R1h > side * c.G1h)) { c.Stage = 2; c.Reason = "종목 " + SPct(c.R1h) + comparison + c.GroupName + " " + SPct(c.G1h); return c; }
            if (!(side * c.G1h > side * c.B1h)) { c.Stage = 2; c.Reason = c.GroupName + " " + SPct(c.G1h) + comparison + c.Bench + " " + SPct(c.B1h); return c; }
            if (!(side * c.B1h > 0)) { c.Stage = 2; c.Reason = c.Bench + " " + SPct(c.B1h) + comparison + "0"; return c; }
            // 3단계: 거래대금 배수
            bool est; int n; string why;
            var vm = VolMult(tk, t, coin, out est, out n, out why);
            c.VolMult = vm; c.VolEst = est; c.VolSamples = n;
            if (!vm.HasValue) { c.Stage = 3; c.Reason = why; return c; }
            if (!(vm > Cfg.VolMultMin)) { c.Stage = 3; c.Reason = "거래대금 " + Mult(vm) + " ≤ " + Mult(Cfg.VolMultMin); return c; }
            // 4단계: 옵션 호가
            double? spot = At(coin ? (c.Chain == "ETH_DERIBIT" ? "ETH" : "BTC") : ChainUnderlying(c.Chain), t) != null
                ? At(coin ? (c.Chain == "ETH_DERIBIT" ? "ETH" : "BTC") : ChainUnderlying(c.Chain), t).C : (double?)null;
            var cr = CheckChain(c.Chain, spot, coin);
            c.Spread = cr.Spread;
            c.ChainNote = cr.Reason;
            if (!cr.Pass) { c.Stage = 4; c.Reason = cr.Reason; return c; }
            c.Stage = 5; c.Reason = "통과 · " + cr.Reason;
            return c;
        }

        static string ChainUnderlying(string chain) { return chain.Replace("_CHAIN", ""); }

        // ---------------- 차트 ----------------
        void BuildCharts(DateTime? tUs, DateTime? tC)
        {
            // 미국: 최근(또는 진행 중) 정규장 하루, SPY 대비 누적 초과수익률
            var spyBars = Bars("SPY");
            if (spyBars.Count > 0)
            {
                var lastT = spyBars[spyBars.Count - 1].T;
                var day = Tz.ToEt(lastT).Date;
                var sess = spyBars.Where(b => Tz.ToEt(b.T).Date == day).ToList();
                M.SectorAxis = "ET " + day.ToString("MM-dd (ddd)", CultureInfo.InvariantCulture) + " 정규장 · SPY 대비 누적 %p";
                var s0 = sess[0];
                foreach (var sec in Catalog.Sectors)
                {
                    var L = new SeriesLine { Key = sec, Name = sec + " " + Catalog.SectorName[sec] };
                    var ix = Ix(sec);
                    Bar b0 = sess.Select(b => At(sec, b.T)).FirstOrDefault(b => b != null);
                    if (b0 != null)
                        foreach (var sb in sess)
                        {
                            var b = At(sec, sb.T); if (b == null) continue;
                            double v = (b.C / b0.O - 1 - (sb.C / s0.O - 1)) * 100;
                            L.Pts.Add(new KeyValuePair<DateTime, double>(sb.End, v));
                        }
                    var tt = tUs ?? lastT;
                    var r = Ret1h(sec, tt); var rb = Ret1h("SPY", tt);
                    if (r.HasValue && rb.HasValue) L.Rs1h = (r - rb) * 100;
                    bool est; int n; string why;
                    L.VolMult = VolMult(sec, tt, false, out est, out n, out why); L.VolEst = est;
                    L.Note = L.VolMult.HasValue ? null : why;
                    M.Sectors.Add(L);
                }
                Rank(M.Sectors);
            }
            else M.SectorAxis = "SPY 5분봉 미수신";

            // 코인: 최근 24시간, BTC 대비 누적 초과수익률(테마 = 구성 종목 중앙값)
            var btcBars = Bars("BTC");
            if (btcBars.Count > 0)
            {
                var lastT = btcBars[btcBars.Count - 1].T;
                var win = btcBars.Where(b => b.T > lastT.AddHours(-24)).ToList();
                var b0 = win[0];
                M.ThemeAxis = "최근 24시간 · BTC 대비 누적 %p (테마 중앙값)";
                foreach (var th in Catalog.Themes)
                {
                    var mem = Catalog.ThemeMembers[th];
                    var L = new SeriesLine { Key = th, Name = Catalog.ThemeName[th] + (mem.Length > 1 ? " " + th : " " + th) };
                    var base0 = mem.ToDictionary(m => m, m => At(m, b0.T));
                    foreach (var bb in win)
                    {
                        if ((bb.T - b0.T).TotalMinutes % 15 != 0 && bb != win[win.Count - 1]) continue;
                        double btcR = bb.C / b0.C - 1;
                        var vals = new List<double>();
                        foreach (var m in mem)
                        {
                            var a = At(m, bb.T); var z = base0[m];
                            if (a != null && z != null) vals.Add((a.C / z.C - 1 - btcR) * 100);
                        }
                        if (vals.Count > 0) L.Pts.Add(new KeyValuePair<DateTime, double>(bb.End, Median(vals)));
                    }
                    var tt = tC ?? lastT;
                    var rb = Ret1h("BTC", tt);
                    var rs = mem.Select(m => Ret1h(m, tt)).Where(x => x.HasValue).Select(x => x.Value).ToList();
                    if (rs.Count > 0 && rb.HasValue) L.Rs1h = (Median(rs) - rb) * 100;
                    var vms = new List<double>(); bool anyEst = false; string why0 = null;
                    foreach (var m in mem)
                    {
                        bool est; int n; string why;
                        var v = VolMult(m, tt, true, out est, out n, out why);
                        if (v.HasValue) vms.Add(v.Value); else why0 = why;
                        anyEst |= est;
                    }
                    if (vms.Count > 0) L.VolMult = Median(vms); else L.Note = why0;
                    L.VolEst = anyEst;
                    M.Themes.Add(L);
                }
                Rank(M.Themes);
            }
            else M.ThemeAxis = "BTC 5분봉 미수신";
        }

        static void Rank(List<SeriesLine> ls)
        {
            int i = 1;
            foreach (var l in ls.Where(x => x.Last.HasValue).OrderByDescending(x => x.Last.Value)) l.Rank = i++;
        }

        // ---------------- 상단 띠 ----------------
        Chip BarChip(string tag, string label)
        {
            var ch = new Chip { Label = label };
            var bars = Bars(tag);
            if (bars.Count == 0) { ch.Value = "미수신"; ch.Missing = true; return ch; }
            var last = bars[bars.Count - 1];
            ch.Value = Num(last.C);
            var d = Tz.ToEt(last.T).Date;
            var prev = bars.LastOrDefault(b => Tz.ToEt(b.T).Date < d);
            if (prev != null)
            {
                double r = last.C / prev.C - 1;
                ch.Delta = SPct(r); ch.Sign = Math.Sign(r);
            }
            else ch.Delta = "이력 부족";
            ch.Note = Tz.Kst(last.End, "MM-dd HH:mm");
            return ch;
        }

        void BuildTopBand()
        {
            M.Commod.Add(BarChip("GLD", "금 GLD"));
            M.Commod.Add(BarChip("SLV", "은 SLV"));
            M.Commod.Add(BarChip("USO", "원유 USO"));
            M.Commod.Add(BarChip("UNG", "가스 UNG"));
            M.Commod.Add(BarChip("CPER", "구리 CPER"));
            M.Commod.Add(BarChip("DBA", "농산물 DBA"));
            M.Commod.Add(BarChip("DXY", "달러 DXY"));
            M.Commod.Add(BarChip("FXY", "엔 FXY"));

            // 금리
            var y = S["US_YIELDS"];
            foreach (var tenor in new[] { "2Y", "10Y" })
            {
                var ch = new Chip { Label = "미 " + tenor };
                var rows = y == null ? new List<Dictionary<string, object>>() : y.Rows.Where(r => string.Equals(Json.Str(r, "tenor"), tenor, StringComparison.OrdinalIgnoreCase) && Json.Num(r, "rate", "value").HasValue)
                    .Select(r => new { t = Json.Time(r, "obs_date", "date") ?? DateTime.MinValue, v = Json.Num(r, "rate", "value").Value })
                    .GroupBy(x => x.t).Select(g => g.Last()).OrderBy(x => x.t).Select(x => new Dictionary<string, object> { { "t", x.t }, { "v", x.v } }).ToList();
                if (rows.Count == 0) { ch.Value = "미수신"; ch.Missing = true; }
                else
                {
                    double v = (double)rows[rows.Count - 1]["v"];
                    ch.Value = F(v, 2) + "%";
                    ch.Note = ((DateTime)rows[rows.Count - 1]["t"]).ToString("MM-dd");
                    if (rows.Count > 1) { double bp = (v - (double)rows[rows.Count - 2]["v"]) * 100; ch.Delta = (bp > 0 ? "+" : bp < 0 ? "−" : "±") + F(Math.Abs(bp), 0) + "bp"; ch.Sign = Math.Sign(bp); }
                    else ch.Delta = "전일 없음";
                }
                M.Rates.Add(ch);
            }
            var fred = S["FRED"];
            var ff = fred == null ? null : fred.Rows.Where(r => Json.Str(r, "series_id") == "FEDFUNDS" && Json.Num(r, "value").HasValue)
                .OrderBy(r => Json.Time(r, "obs_date") ?? DateTime.MinValue).LastOrDefault();
            M.Rates.Add(ff == null ? new Chip { Label = "기준금리", Value = "미수신", Missing = true }
                : new Chip { Label = "기준금리", Value = F(Json.Num(ff, "value").Value, 2) + "%", Delta = "월평균", Note = (Json.Time(ff, "obs_date") ?? DateTime.MinValue).ToString("yyyy-MM") });
            M.Rates.Add(BarChip("VIX", "VIX"));
            double? v9, v3; DateTime? od; VixTerm(out v9, out v3, out od);
            M.Rates.Add(v9.HasValue ? new Chip { Label = "VIX9D/3M", Value = F(v9.Value, 1) + "/" + F(v3.Value, 1), Delta = v9 <= v3 ? "정상" : "역전", Sign = v9 <= v3 ? 1 : -1, Note = od.Value.ToString("MM-dd") }
                : new Chip { Label = "VIX9D/3M", Value = "확인 대기", Missing = true });
            M.Rates.Add(BarChip("TLT", "장기채 TLT"));
            M.Rates.Add(BarChip("HYG", "하이일드 HYG"));
        }

        // ---------------- 일정 ----------------
        static readonly Regex TimeRx = new Regex(@"(\d{1,2})[:.](\d{2})\s*(a\.?\s*m\.?|p\.?\s*m\.?)?", RegexOptions.IgnoreCase | RegexOptions.Compiled);

        static TimeSpan? ParseClock(string s)
        {
            if (string.IsNullOrEmpty(s)) return null;
            var m = TimeRx.Match(s);
            if (!m.Success) return null;
            int h = int.Parse(m.Groups[1].Value), mi = int.Parse(m.Groups[2].Value);
            var ap = m.Groups[3].Value.ToLowerInvariant();
            if (ap.StartsWith("p") && h < 12) h += 12;
            if (ap.StartsWith("a") && h == 12) h = 0;
            if (h > 23 || mi > 59) return null;
            return new TimeSpan(h, mi, 0);
        }

        List<EventItem> BuildEvents()
        {
            var list = new Dictionary<string, EventItem>();
            Action<string, string, string, string, TimeZoneInfo> add = (tag, name, region, timeKey, zone) =>
            {
                var sd = S[tag]; if (sd == null) return;
                foreach (var r in sd.Rows)
                {
                    string ev = Json.Str(r, "event") ?? "";
                    string nm = name;
                    if (tag == "BLS")
                    {
                        if (ev.IndexOf("CPI", StringComparison.OrdinalIgnoreCase) >= 0 || ev.IndexOf("Consumer Price", StringComparison.OrdinalIgnoreCase) >= 0) nm = "미국 CPI";
                        else if (ev.IndexOf("Employment", StringComparison.OrdinalIgnoreCase) >= 0 || ev.IndexOf("Nonfarm", StringComparison.OrdinalIgnoreCase) >= 0) nm = "미국 고용보고서";
                        else continue;
                    }
                    DateTime d;
                    if (!DateTime.TryParseExact(Json.Str(r, "release_date") ?? "", "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d)) continue;
                    string tm = Json.Str(r, timeKey, "release_time", "time");
                    var clock = ParseClock(tm);
                    var e = new EventItem { Name = nm, Region = region, Date = d, Raw = tm, SourceTag = tag };
                    if (clock.HasValue) e.WhenUtc = Tz.FromLocal(d + clock.Value, zone);
                    list[nm + "|" + d.ToString("yyyyMMdd")] = e;     // 나중 행(갱신)이 우선
                }
            };
            add("BLS", "미국 지표", "미국", "release_time_et", Tz.ET);
            add("FOMC", "FOMC", "미국", "release_time_et", Tz.ET);
            add("ECB", "ECB 금리", "유로", "release_time_cet", Tz.CET);
            var etToday = Tz.ToEt(now).Date;
            var res = list.Values
                .Where(e => e.WhenUtc.HasValue ? e.WhenUtc.Value.AddMinutes(Cfg.EventAfterMin) >= now : e.Date >= etToday)
                .OrderBy(e => e.WhenUtc ?? e.Date.AddHours(12)).ToList();
            foreach (var e in res)
            {
                e.Today = e.Date == etToday || (e.Region == "유로" && e.Date == TimeZoneInfo.ConvertTimeFromUtc(now, Tz.CET).Date);
                if (e.Today && !e.WhenUtc.HasValue) M.EventWarnings.Add(e.Name + " 오늘 발표 · 시각 미정");
            }
            M.Events = res;
            M.NextEvent = res.FirstOrDefault(e => e.WhenUtc.HasValue && e.WhenUtc > now) ?? res.FirstOrDefault();
            return res;
        }

        // ---------------- 뉴스 ----------------
        void BuildNews()
        {
            foreach (var pair in new[] { new[] { "BBC_WORLD", "BBC" }, new[] { "COINDESK", "CoinDesk" } })
            {
                var sd = S[pair[0]]; if (sd == null) continue;
                var seen = new HashSet<string>();
                foreach (var r in sd.Rows)
                {
                    var title = Json.Str(r, "title"); if (title == null) continue;
                    var link = Json.Str(r, "link") ?? title;
                    if (!seen.Add(link)) continue;
                    var n = new NewsItem { Src = pair[1], Title = title, Link = link, Pub = Json.Time(r, "published_at", "pubDate", "published") ?? Json.Time(r, "received_at_utc") };
                    if (pair[1] == "BBC")
                    {
                        var lt = " " + title.ToLowerInvariant() + " ";
                        n.Flag = Cfg.GeoWords.Any(w => lt.Contains(w));
                    }
                    M.News.Add(n);
                }
            }
            M.News = M.News.OrderByDescending(n => n.Pub ?? DateTime.MinValue).ToList();
        }

        // ---------------- 옵션 위험 지표 ----------------
        void BuildRisk()
        {
            // 풋/콜 비율 3종(합산하지 않음)
            Func<string, string, string> pcr = (tag, metric) =>
            {
                var sd = S[tag]; if (sd == null || sd.Rows.Count == 0) return null;
                var rows = sd.Rows.Where(r =>
                {
                    var m = (Json.Str(r, "metric", "ratio_type") ?? "").ToLowerInvariant();
                    return metric == null || m.Length == 0 || m.Contains(metric);
                }).Select(r => new { t = Json.Time(r, "obs_date", "date", "selectedDate", "obs_time") ?? Json.Time(r, "received_at_utc") ?? DateTime.MinValue, v = Json.Num(r, "ratio", "value") })
                .Where(x => x.v.HasValue).GroupBy(x => x.t.Date).Select(g => g.Last()).OrderBy(x => x.t).ToList();
                if (rows.Count == 0) return null;
                var l = rows[rows.Count - 1];
                string s = F(l.v.Value, 2) + " · " + l.t.ToString("MM-dd");
                if (tag == "PCR_EQUITY" && rows.Count >= 21)
                {
                    var p90 = Percentile(rows.Skip(rows.Count - 21).Take(20).Select(x => x.v.Value).ToList(), 0.9);
                    if (l.v > p90) s += " · 개별주 풋 거래 비중 높음";
                }
                return s;
            };
            M.Risk.Add(new RiskItem { Label = "풋/콜 전체·71", Value = pcr("PCR_TOTAL", "total") ?? "미수신" });
            M.Risk.Add(new RiskItem { Label = "풋/콜 개별주·72", Value = pcr("PCR_EQUITY", "equity") ?? "미수신" });
            M.Risk.Add(new RiskItem { Label = "풋/콜 지수·73", Value = pcr("PCR_INDEX", "index") ?? "미수신" });
            Func<string, string> snap = tag =>
            {
                var sd = S[tag]; if (sd == null || sd.Rows.Count == 0) return "미수신";
                var r = sd.Rows.Where(x => Json.Num(x, "value", "close").HasValue).OrderBy(x => Json.Time(x, "obs_time", "obs_date", "date") ?? DateTime.MinValue).LastOrDefault();
                if (r == null) return "미수신";
                return F(Json.Num(r, "value", "close").Value, 2) + " · " + Tz.Kst(Json.Time(r, "obs_time", "obs_date", "date"), "MM-dd");
            };
            M.Risk.Add(new RiskItem { Label = "SKEW·75", Value = snap("SKEW") });
            M.Risk.Add(new RiskItem { Label = "VVIX·99", Value = snap("VVIX") });
            double? v9, v3; DateTime? od; VixTerm(out v9, out v3, out od);
            M.Risk.Add(new RiskItem { Label = "VIX9D/3M·74", Value = v9.HasValue ? F(v9.Value, 2) + " / " + F(v3.Value, 2) + (v9 > v3 ? " 역전" : " 정상") : "확인 대기", Note = od.HasValue ? "관측일 " + od.Value.ToString("yyyy-MM-dd") : "동일 관측일·최근 5일의 유효 값 필요", Sign = v9.HasValue ? (v9 > v3 ? -1 : 1) : 0 });
            var vixOptions = S["VIX_OPTIONS"];
            if (vixOptions == null || vixOptions.Opts.Count == 0)
            {
                M.Risk.Add(new RiskItem { Label = "VIX 옵션 거래량·77", Value = vixOptions != null && vixOptions.Total > 0 ? "원본 시각 확인 대기" : "미수신",
                    Note = vixOptions != null && vixOptions.LastRecv.HasValue ? "수신 " + Tz.Kst(vixOptions.LastRecv, "MM-dd HH:mm") + " KST · 원본 관측일·유효 계약 미확인" : "유효 계약의 원본 관측시각 필요" });
            }
            else
            {
                var vixSnap = vixOptions.Opts.Max(o => o.Snap);
                var vixRows = vixOptions.Opts.Where(o => o.Snap == vixSnap)
                    .GroupBy(o => new { o.Exp, o.Strike, o.Call }).Select(g => g.Last()).ToList();
                var vols = vixRows.Where(o => o.Volume.HasValue && o.Volume >= 0).ToList();
                M.Risk.Add(new RiskItem { Label = "VIX 옵션 거래량·77", Value = vixSnap > now ? "원본 시각 확인 대기" : vols.Count == 0 ? "거래량 미수신" : vols.Sum(o => o.Volume.Value).ToString("N0", CultureInfo.InvariantCulture),
                    Note = "관측 " + Tz.Kst(vixSnap, "MM-dd HH:mm") + " KST · 수집 계약 " + vols.Count + "/" + vixRows.Count + "개 범위" + ((now - vixSnap).TotalMinutes > Cfg.UsChainMaxAgeMin ? " · 지연" : "") });
            }
            foreach (var p in new[] { new[] { "SPX_CHAIN", "SPX·76", "SPX" }, new[] { "RUT_CHAIN", "RUT·78", "RUT" },
                new[] { "SPY_CHAIN", "SPY·79", "SPY" }, new[] { "QQQ_CHAIN", "QQQ·80", "QQQ" },
                new[] { "DIA_CHAIN", "DIA·81", "DIA" }, new[] { "IWM_CHAIN", "IWM·82", "IWM" } })
                M.Risk.Add(ChainRisk(p[0], p[1], p[2], false));
            foreach (var p in new[] { new[] { "BTC_DERIBIT", "BTC IV·101", "BTC" }, new[] { "ETH_DERIBIT", "ETH IV·102", "ETH" } })
                M.Risk.Add(ChainRisk(p[0], p[1], p[2], true));
            var macro = new List<string>();
            foreach (var u in new[] { "TLT", "GLD", "USO", "EEM", "SLV" })
            {
                var spot = Bars(u).LastOrDefault();
                var cr = CheckChain(u + "_CHAIN", spot != null ? spot.C : (double?)null, false);
                macro.Add(u + " " + (cr.AtmIv.HasValue ? F(cr.AtmIv.Value, 0) : "—"));
            }
            M.Risk.Add(new RiskItem { Label = "ATM IV 83~90", Value = string.Join("  ", macro) });
            // 만기
            var occ = S["OCC_EXPIRY"];
            var nxt = occ == null ? null : occ.Rows.Select(r => new { d = Json.Str(r, "expiration_date"), t = Json.Str(r, "expiry_type") })
                .Where(x => x.d != null && string.CompareOrdinal(x.d, Tz.ToEt(now).ToString("yyyy-MM-dd")) >= 0).OrderBy(x => x.d).FirstOrDefault();
            M.Risk.Add(new RiskItem { Label = "다음 만기·100", Value = nxt != null ? nxt.d + " " + (nxt.t ?? "") : "미수신" });
        }

        RiskItem ChainRisk(string tag, string label, string underlying, bool coin)
        {
            var spot = Bars(underlying).LastOrDefault();
            var cr = CheckChain(tag, spot != null ? spot.C : (double?)null, coin);
            var sd = S[tag];
            string value;
            if (!cr.Snap.HasValue) value = sd != null && sd.Total > 0 ? "원본 시각 확인 대기" : "미수신";
            else if (cr.Snap.Value > now) value = "원본 시각 확인 대기";
            else value = "SPR " + (cr.Spread.HasValue ? Pct(cr.Spread.Value, 1) : "—") + " · IV " + (cr.AtmIv.HasValue ? F(cr.AtmIv.Value, 1) + "%" : "—");
            string note = cr.Snap.HasValue ? "관측 " + Tz.Kst(cr.Snap, "MM-dd HH:mm") + " KST" :
                sd != null && sd.LastRecv.HasValue ? "수신 " + Tz.Kst(sd.LastRecv, "MM-dd HH:mm") + " KST · 원본 관측시각 미확인" : "";
            return new RiskItem { Label = label, Value = value, Note = note + (note.Length > 0 ? " · " : "") + cr.Reason,
                Sign = cr.Pass ? 1 : cr.Spread.HasValue ? -1 : 0 };
        }

        // ---------------- 수신 상태 ----------------
        void BuildStatus()
        {
            bool usOpen = Tz.UsOpen(now);
            foreach (var s in Catalog.All)
            {
                var sd = S[s.Tag];
                var row = new StatusRow { Src = s, Valid = sd.Valid, Total = sd.Total, Provider = sd.Providers.Count > 0 ? string.Join(" / ", sd.Providers.OrderBy(p => p, StringComparer.Ordinal)) : "미수신" };
                DateTime? obs = sd.LastObs;
                if (s.Kind == Kind.UsBar || s.Kind == Kind.CoinBar || s.Kind == Kind.FxBar) obs = sd.Last != null ? sd.Last.End : (DateTime?)null;
                row.LastObs = obs.HasValue ? Tz.Kst(obs, "MM-dd HH:mm") : "—";
                if (!sd.FolderExists) { row.State = "미수신"; row.Reason = "data/" + s.Tag + " 폴더 없음"; }
                else if (sd.Valid == 0) { row.State = "미수신"; row.Reason = sd.Total == 0 ? "레코드 없음" : "유효 행 0 · " + (sd.LastError ?? "오류"); }
                else
                {
                    double age = obs.HasValue ? (now - obs.Value).TotalMinutes : double.MaxValue;
                    double lim; string note = null;
                    switch (s.Kind)
                    {
                        case Kind.UsBar: lim = usOpen ? Cfg.BarStaleMin : 4 * 1440; note = usOpen ? null : "장외"; break;
                        case Kind.FxBar: lim = (Tz.ToEt(now).DayOfWeek == DayOfWeek.Saturday || Tz.ToEt(now).DayOfWeek == DayOfWeek.Sunday) ? 3 * 1440 : 30; break;
                        case Kind.CoinBar: lim = Cfg.BarStaleMin; break;
                        case Kind.UsChain: lim = usOpen ? Cfg.UsChainMaxAgeMin : 4 * 1440; note = usOpen ? null : "장외"; break;
                        case Kind.CoinChain: lim = Cfg.CoinChainMaxAgeMin; break;
                        case Kind.News: obs = sd.LastRecv ?? obs; age = obs.HasValue ? (now - obs.Value).TotalMinutes : double.MaxValue; lim = 180; break;
                        case Kind.Calendar: obs = sd.LastRecv ?? obs; age = obs.HasValue ? (now - obs.Value).TotalMinutes : double.MaxValue; lim = 3 * 1440; break;
                        default: lim = 5 * 1440; break;   // 일간: 다음 공표 전까지
                    }
                    if (age < 0) { row.State = "확인 대기"; row.Reason = "원본 관측·수신 시각이 미래"; }
                    else if (age <= lim) { row.Ok = true; row.State = note == null ? "정상" : "정상·" + note; row.Reason = sd.Errors > 0 ? "오류행 " + sd.Errors + " 제외" : ""; }
                    else { row.State = "지연"; row.Reason = "마지막 원본 " + Ago(age) + " 전" + (sd.LastError != null ? " · " + sd.LastError : ""); }
                }
                M.Status.Add(row);
            }
            M.BaseOk = M.Status.Count(r => r.Ok && r.Src.No <= 70);
            M.OptOk = M.Status.Count(r => r.Ok && r.Src.No > 70);
        }

        static string Ago(double min)
        {
            if (min > 1e8) return "?";
            if (min < 90) return (int)min + "분";
            if (min < 2880) return (int)(min / 60) + "시간";
            return (int)(min / 1440) + "일";
        }
    }
}
