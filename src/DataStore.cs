using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace MarketDash
{
    public class Bar
    {
        public DateTime T;          // 봉 시작 UTC
        public double O, H, L, C;
        public double? V, Vwap, Quote;
        public DateTime End { get { return T.AddMinutes(5); } }
    }

    public class OptRow
    {
        public DateTime Snap;       // 스냅샷(원본) 시각 UTC
        public DateTime? Exp;       // 만기일(UTC 자정 또는 만기 시각)
        public double Strike;
        public bool Call;
        public double? Bid, Ask, Iv, Under, Volume;
    }

    class FileState
    {
        public long Offset;
        public DateTime Write, Created;
        public byte[] Prefix;
        public List<Bar> Bars = new List<Bar>();
        public List<OptRow> Opts = new List<OptRow>();
        public List<Dictionary<string, object>> Rows = new List<Dictionary<string, object>>();
        public int Total, Valid, Errors;
        public string LastError;
        public DateTime? LastObs, LastRecv;
        public HashSet<string> Providers = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        public void Reset()
        {
            Offset = 0; Prefix = null;
            Bars.Clear(); Opts.Clear(); Rows.Clear(); Providers.Clear();
            Total = Valid = Errors = 0;
            LastError = null; LastObs = LastRecv = null;
        }
    }

    /// <summary>한 원천(data/&lt;Tag&gt;)의 누적 상태.</summary>
    public class SourceData
    {
        public Source Src;
        public bool FolderExists;
        internal Dictionary<string, FileState> Files = new Dictionary<string, FileState>(StringComparer.OrdinalIgnoreCase);
        public List<Bar> Bars = new List<Bar>();                       // 시간순, 중복 제거
        public List<OptRow> Opts = new List<OptRow>();
        public List<Dictionary<string, object>> Rows = new List<Dictionary<string, object>>();
        public int Total, Valid, Errors;
        public string LastError;
        public DateTime? LastObs, LastRecv;
        public HashSet<string> Providers = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        internal bool Dirty = true;
        internal DateTime? NextBarEnd;

        public Bar Last { get { return Bars.Count > 0 ? Bars[Bars.Count - 1] : null; } }
    }

    /// <summary>
    /// 수집기 JSONL 을 공유 읽기(FileShare.ReadWrite|Delete)로 열어 추가된 바이트만 반영한다.
    /// 수집기의 쓰기를 막지 않고, 파일이 교체·축소되면 해당 파일만 다시 읽는다.
    /// </summary>
    public class DataStore
    {
        public string Root;
        public readonly Dictionary<string, SourceData> Map = new Dictionary<string, SourceData>();
        public int FilesRead, BytesRead;

        public DataStore(string root)
        {
            Root = root;
            foreach (var s in Catalog.All) Map[s.Tag] = new SourceData { Src = s };
        }

        public SourceData this[string tag] { get { SourceData d; return Map.TryGetValue(tag, out d) ? d : null; } }

        static readonly Regex DateName = new Regex(@"^(\d{4})-(\d{2})-(\d{2})", RegexOptions.Compiled);

        int KeepDays(Kind k)
        {
            switch (k)
            {
                case Kind.UsBar: case Kind.FxBar: return 40;   // 20거래일 + 여유
                case Kind.CoinBar: return 23;                 // 20일 + 여유
                case Kind.UsChain: return 6;                  // 최근 거래일 스냅샷
                case Kind.CoinChain: return 2;
                case Kind.News: return 3;
                default: return 400;                          // 일간·일정은 작다
            }
        }

        public void Refresh(DateTime nowUtc)
        {
            FilesRead = 0; BytesRead = 0;
            foreach (var sd in Map.Values)
            {
                try { RefreshSource(sd, nowUtc); }
                catch (Exception ex) { sd.LastError = "읽기 오류: " + ex.Message; }
                if (sd.Dirty || (sd.NextBarEnd.HasValue && sd.NextBarEnd.Value <= nowUtc))
                { Rebuild(sd, nowUtc); sd.Dirty = false; }
            }
        }

        void RefreshSource(SourceData sd, DateTime nowUtc)
        {
            string dir = Path.Combine(Root, sd.Src.Tag);
            sd.FolderExists = Directory.Exists(dir);
            if (!sd.FolderExists)
            {
                if (sd.Files.Count > 0) { sd.Files.Clear(); sd.Dirty = true; }
                return;
            }
            DateTime cutoff = nowUtc.Date.AddDays(-KeepDays(sd.Src.Kind));
            var present = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var path in Directory.GetFiles(dir, "*.jsonl"))
            {
                string name = Path.GetFileName(path);
                var m = DateName.Match(name);
                if (m.Success)
                {
                    DateTime fd;
                    if (DateTime.TryParseExact(m.Value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out fd) && fd < cutoff)
                        continue;
                }
                present.Add(name);
                var fi = new FileInfo(path);
                FileState fs;
                bool isNew = !sd.Files.TryGetValue(name, out fs);
                if (isNew) { fs = new FileState(); sd.Files[name] = fs; }
                long len = fi.Length;
                if (!isNew && len == fs.Offset && fi.LastWriteTimeUtc == fs.Write && fi.CreationTimeUtc == fs.Created) continue;
                if (len < fs.Offset || (!isNew && fi.CreationTimeUtc != fs.Created) ||
                    (!isNew && len == fs.Offset && fi.LastWriteTimeUtc != fs.Write))
                {
                    fs = new FileState(); sd.Files[name] = fs;       // 교체·축소 → 처음부터
                }
                ReadAppend(path, fs, sd.Src);
                fs.Write = fi.LastWriteTimeUtc;
                fs.Created = fi.CreationTimeUtc;
                sd.Dirty = true;
            }
            foreach (var k in sd.Files.Keys.ToList())
                if (!present.Contains(k)) { sd.Files.Remove(k); sd.Dirty = true; }
        }

        void ReadAppend(string path, FileState fs, Source src)
        {
            byte[] buf;
            using (var st = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16))
            {
                if (fs.Offset > st.Length) fs.Reset();
                if (fs.Prefix != null)
                {
                    // 길이가 커진 교체 파일도 기존 파일의 앞부분과 다르면 처음부터 읽는다.
                    byte[] current = new byte[fs.Prefix.Length];
                    int count = st.Read(current, 0, current.Length);
                    if (count != fs.Prefix.Length || !current.SequenceEqual(fs.Prefix)) fs.Reset();
                }
                if (fs.Prefix == null && st.Length > 0)
                {
                    st.Seek(0, SeekOrigin.Begin);
                    fs.Prefix = new byte[(int)Math.Min(256, st.Length)];
                    int count = st.Read(fs.Prefix, 0, fs.Prefix.Length);
                    if (count < fs.Prefix.Length) Array.Resize(ref fs.Prefix, count);
                }
                st.Seek(fs.Offset, SeekOrigin.Begin);
                long n = st.Length - fs.Offset;
                if (n <= 0) return;
                buf = new byte[n];
                int got = 0;
                while (got < n) { int r = st.Read(buf, got, (int)(n - got)); if (r <= 0) break; got += r; }
                if (got < n) Array.Resize(ref buf, got);
            }
            int lastNl = Array.LastIndexOf(buf, (byte)'\n');
            if (lastNl < 0) return;                                 // 아직 완성된 줄 없음(쓰는 중)
            FilesRead++; BytesRead += lastNl + 1;
            int start = 0;
            if (fs.Offset == 0 && buf.Length >= 3 && buf[0] == 0xEF && buf[1] == 0xBB && buf[2] == 0xBF) start = 3;
            string text = Encoding.UTF8.GetString(buf, start, lastNl + 1 - start);
            fs.Offset += lastNl + 1;
            foreach (var raw in text.Split('\n'))
            {
                var line = raw.Trim();
                if (line.Length == 0) continue;
                fs.Total++;
                var d = Json.ParseObject(line);
                if (d == null) { fs.Errors++; fs.LastError = "JSON 형식 오류"; continue; }
                ParseRow(d, fs, src);
            }
        }

        static readonly HashSet<string> OkStatus = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        { "ok", "scheduled", "updated", "oi_unavailable", "delayed", "new", "confirmed" };

        static bool IsErrorStatus(string st)
        {
            if (string.IsNullOrEmpty(st)) return false;
            return !OkStatus.Contains(st);
        }

        void ParseRow(Dictionary<string, object> d, FileState fs, Source src)
        {
            foreach (string key in new[] { "source", "provider" })
            {
                string provider = Json.Str(d, key);
                if (!string.IsNullOrWhiteSpace(provider)) fs.Providers.Add(provider.Trim());
            }
            string st = Json.Str(d, "status");
            var recv = Json.Time(d, "received_at_utc", "collected_at", "collected_at_utc", "fetched_at");
            if (recv.HasValue && (!fs.LastRecv.HasValue || recv > fs.LastRecv)) fs.LastRecv = recv;

            switch (src.Kind)
            {
                case Kind.UsBar: case Kind.CoinBar: case Kind.FxBar:
                {
                    var t = Json.Time(d, "bar_open_utc", "t", "open_time_iso", "open_time", "open_ts", "open_ms", "open_time_ms", "ts");
                    double? c = Json.Num(d, "c", "close");
                    bool validBarStatus = !IsErrorStatus(st)
                        || string.Equals(st, "final", StringComparison.OrdinalIgnoreCase)
                        || string.Equals(st, "completed", StringComparison.OrdinalIgnoreCase)
                        || (src.Kind == Kind.CoinBar && string.Equals(st, "ok_oi_null", StringComparison.OrdinalIgnoreCase));
                    if (!t.HasValue || !c.HasValue || c.Value <= 0 || !validBarStatus)
                    {
                        fs.Errors++; fs.LastError = st ?? "필드 누락"; return;
                    }
                    var b = new Bar
                    {
                        T = t.Value, C = c.Value,
                        O = Json.Num(d, "o", "open") ?? c.Value, H = Json.Num(d, "h", "high") ?? c.Value, L = Json.Num(d, "l", "low") ?? c.Value,
                        V = Json.Num(d, "v", "volume", "base_vol"),
                        Vwap = Json.Num(d, "vwap", "vw"),
                        Quote = Json.Num(d, "quote_vol", "quote_volume", "turnover", "quote_v")
                    };
                    fs.Bars.Add(b); fs.Valid++;
                    if (!fs.LastObs.HasValue || b.T > fs.LastObs) fs.LastObs = b.T;
                    return;
                }
                case Kind.UsChain: case Kind.CoinChain:
                {
                    // AMD의 기존 ts는 수집기 로컬 시각이다. 원천 스냅샷 시각이 붙은 계약만 판정한다.
                    if (src.No == 98 && !Json.Time(d, "snapshot_at_utc").HasValue)
                    {
                        fs.Errors++; fs.LastError = "AMD 원본 스냅샷 시각 없음"; return;
                    }
                    // 미국 체인은 원본 snapshot/quote 시각이 필수. 현재 Deribit 수집기만 수신시각을 스냅샷으로 기록한다.
                    var o = ParseOpt(d, src.No == 101 || src.No == 102 ? recv : null);
                    if (o == null || (st != null && IsErrorStatus(st)))
                    {
                        fs.Errors++;
                        fs.LastError = IsErrorStatus(st) ? st : "원본 스냅샷·계약 필드 누락";
                        return;
                    }
                    fs.Opts.Add(o); fs.Valid++;
                    if (!fs.LastObs.HasValue || o.Snap > fs.LastObs) fs.LastObs = o.Snap;
                    return;
                }
                default:
                {
                    bool valid;
                    DateTime? obs;
                    if (src.Kind == Kind.News)
                    {
                        valid = Json.Str(d, "title") != null && !IsErrorStatus(st);
                        obs = Json.Time(d, "published_at", "pubDate", "published");
                    }
                    else if (src.Kind == Kind.Calendar)
                    {
                        valid = Json.Str(d, "release_date", "expiration_date", "date") != null && !IsErrorStatus(st);
                        obs = recv;
                    }
                    else
                    {
                        valid = Json.Num(d, "value", "rate", "ratio", "close", "c") != null && !IsErrorStatus(st);
                        obs = Json.Time(d, "obs_time", "obs_date", "date", "selectedDate", "bar_open_utc");
                    }
                    if (!valid) { fs.Errors++; fs.LastError = st ?? "값 없음"; return; }
                    fs.Rows.Add(d); fs.Valid++;
                    if (obs.HasValue && (!fs.LastObs.HasValue || obs > fs.LastObs)) fs.LastObs = obs;
                    return;
                }
            }
        }

        static readonly Regex Occ = new Regex(@"(\d{6})([CP])(\d{8})$", RegexOptions.Compiled);
        static readonly Regex Deribit = new Regex(@"-(\d{1,2}[A-Z]{3}\d{2})-([\d.]+)-([CP])$", RegexOptions.Compiled);

        static OptRow ParseOpt(Dictionary<string, object> d, DateTime? recv)
        {
            var o = new OptRow();
            string name = Json.Str(d, "instrument_name", "instrument", "option_symbol", "contract", "symbol_option", "occ_symbol");
            double? strike = Json.Num(d, "strike", "strike_price");
            string type = Json.Str(d, "option_type", "type", "right", "put_call", "cp", "side");
            DateTime? exp = ParseExp(Json.Get(d, "expiration", "expiry", "expiration_date", "expiry_date", "exp", "expiration_timestamp"));
            if (name != null)
            {
                var m = Deribit.Match(name);
                if (m.Success)
                {
                    DateTime e;
                    if (!exp.HasValue && DateTime.TryParseExact(m.Groups[1].Value, new[] { "dMMMyy", "ddMMMyy" }, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out e))
                        exp = DateTime.SpecifyKind(e.Date.AddHours(8), DateTimeKind.Utc);    // Deribit 만기 08:00 UTC
                    if (!strike.HasValue) strike = double.Parse(m.Groups[2].Value, CultureInfo.InvariantCulture);
                    if (type == null) type = m.Groups[3].Value;
                }
                else
                {
                    var q = Occ.Match(name.Replace(" ", ""));
                    if (q.Success)
                    {
                        DateTime e;
                        if (!exp.HasValue && DateTime.TryParseExact(q.Groups[1].Value, "yyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out e))
                            exp = DateTime.SpecifyKind(e, DateTimeKind.Utc);
                        if (!strike.HasValue) strike = double.Parse(q.Groups[3].Value, CultureInfo.InvariantCulture) / 1000.0;
                        if (type == null) type = q.Groups[2].Value;
                    }
                }
            }
            if (!strike.HasValue || type == null) return null;
            type = type.Trim().ToLowerInvariant();
            if (type.StartsWith("c")) o.Call = true;
            else if (type.StartsWith("p")) o.Call = false;
            else return null;
            o.Strike = strike.Value;
            o.Exp = exp;
            o.Bid = Json.Num(d, "bid", "bid_price", "best_bid_price");
            o.Ask = Json.Num(d, "ask", "ask_price", "best_ask_price");
            o.Volume = Json.Num(d, "volume", "v", "trade_volume");
            o.Iv = Json.Num(d, "iv", "implied_volatility", "mark_iv", "mid_iv", "smv_vol");
            o.Under = Json.Num(d, "underlying_price", "underlying", "spot", "index_price", "underlying_last", "spot_price");
            var snap = Json.Time(d, "snapshot_at_utc", "snapshot_ts", "snapshot_utc", "snapshot", "quote_utc", "quote_time_utc", "quote_time", "ts_utc", "ts", "timestamp");
            o.Snap = snap ?? recv ?? DateTime.MinValue;
            if (o.Snap == DateTime.MinValue) return null;
            return o;
        }

        static DateTime? ParseExp(object v)
        {
            if (v == null) return null;
            var s = v as string;
            if (s != null)
            {
                s = s.Trim();
                DateTime e;
                if (DateTime.TryParseExact(s, new[] { "yyyy-MM-dd", "yyyyMMdd", "yyMMdd", "dMMMyy", "ddMMMyy", "yyyy/MM/dd" }, CultureInfo.InvariantCulture,
                    DateTimeStyles.None, out e))
                    return DateTime.SpecifyKind(e, DateTimeKind.Utc);
            }
            return Json.ToTime(v);
        }

        void Rebuild(SourceData sd, DateTime nowUtc)
        {
            sd.Total = sd.Valid = sd.Errors = 0; sd.LastObs = sd.LastRecv = null; sd.LastError = null;
            sd.Providers.Clear();
            var bars = new Dictionary<DateTime, Bar>();
            var opts = new List<OptRow>();
            var rows = new List<Dictionary<string, object>>();
            foreach (var kv in sd.Files.OrderBy(k => k.Key, StringComparer.Ordinal))
            {
                var fs = kv.Value;
                sd.Total += fs.Total; sd.Valid += fs.Valid; sd.Errors += fs.Errors;
                sd.Providers.UnionWith(fs.Providers);
                if (fs.LastError != null) sd.LastError = fs.LastError;
                if (fs.LastObs.HasValue && (!sd.LastObs.HasValue || fs.LastObs > sd.LastObs)) sd.LastObs = fs.LastObs;
                if (fs.LastRecv.HasValue && (!sd.LastRecv.HasValue || fs.LastRecv > sd.LastRecv)) sd.LastRecv = fs.LastRecv;
                foreach (var b in fs.Bars) bars[b.T] = b;                 // 원본 시각 기준 중복 제거(나중 행 우선)
                opts.AddRange(fs.Opts);
                rows.AddRange(fs.Rows);
            }
            // 완성봉만: 봉 종료 시각이 현재 이전
            sd.Bars = bars.Values.Where(b => b.End <= nowUtc).OrderBy(b => b.T).ToList();
            sd.NextBarEnd = bars.Values.Where(b => b.End > nowUtc).Select(b => (DateTime?)b.End).Min();
            if (sd.Src.Kind == Kind.UsBar || sd.Src.Kind == Kind.CoinBar || sd.Src.Kind == Kind.FxBar)
                sd.LastObs = sd.Bars.Count > 0 ? (DateTime?)sd.Bars[sd.Bars.Count - 1].T : null;
            sd.Opts = opts;
            sd.Rows = rows;
        }
    }
}
