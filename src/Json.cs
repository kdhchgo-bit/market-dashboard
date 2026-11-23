using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace MarketDash
{
    /// <summary>JSONL 한 줄을 Dictionary/List/string/double/bool/null 로 파싱하는 경량 파서.</summary>
    public static class Json
    {
        public static Dictionary<string, object> ParseObject(string s)
        {
            if (string.IsNullOrWhiteSpace(s)) return null;
            int i = 0;
            try
            {
                Skip(s, ref i);
                if (i >= s.Length || s[i] != '{') return null;
                return ReadValue(s, ref i) as Dictionary<string, object>;
            }
            catch { return null; }
        }

        static void Skip(string s, ref int i)
        {
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
        }

        static object ReadValue(string s, ref int i)
        {
            Skip(s, ref i);
            char c = s[i];
            if (c == '{')
            {
                var d = new Dictionary<string, object>(StringComparer.Ordinal);
                i++; Skip(s, ref i);
                if (s[i] == '}') { i++; return d; }
                while (true)
                {
                    Skip(s, ref i);
                    string k = ReadString(s, ref i);
                    Skip(s, ref i);
                    if (s[i] != ':') throw new FormatException();
                    i++;
                    d[k] = ReadValue(s, ref i);
                    Skip(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == '}') { i++; return d; }
                    throw new FormatException();
                }
            }
            if (c == '[')
            {
                var l = new List<object>();
                i++; Skip(s, ref i);
                if (s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(ReadValue(s, ref i));
                    Skip(s, ref i);
                    if (s[i] == ',') { i++; continue; }
                    if (s[i] == ']') { i++; return l; }
                    throw new FormatException();
                }
            }
            if (c == '"') return ReadString(s, ref i);
            if (c == 't' && string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (c == 'f' && string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (c == 'n' && string.CompareOrdinal(s, i, "null", 0, 4) == 0) { i += 4; return null; }
            if (c == 'N' && string.CompareOrdinal(s, i, "NaN", 0, 3) == 0) { i += 3; return null; }
            if (c == 'I' && string.CompareOrdinal(s, i, "Infinity", 0, 8) == 0) { i += 8; return null; }
            int st = i;
            if (s[i] == '-' && i + 1 < s.Length && s[i + 1] == 'I') { i += 9; return null; }
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            double v;
            if (double.TryParse(s.Substring(st, i - st), NumberStyles.Float, CultureInfo.InvariantCulture, out v)) return v;
            throw new FormatException();
        }

        static string ReadString(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException();
            i++;
            StringBuilder sb = null;
            int st = i;
            while (true)
            {
                char c = s[i];
                if (c == '"')
                {
                    string r = sb == null ? s.Substring(st, i - st) : sb.Append(s, st, i - st).ToString();
                    i++;
                    return r;
                }
                if (c == '\\')
                {
                    if (sb == null) sb = new StringBuilder();
                    sb.Append(s, st, i - st);
                    i++;
                    char e = s[i];
                    switch (e)
                    {
                        case 'n': sb.Append('\n'); break;
                        case 't': sb.Append('\t'); break;
                        case 'r': sb.Append('\r'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'u':
                            sb.Append((char)int.Parse(s.Substring(i + 1, 4), NumberStyles.HexNumber));
                            i += 4; break;
                        default: sb.Append(e); break;
                    }
                    i++;
                    st = i;
                    continue;
                }
                i++;
            }
        }

        // ---------- 필드 접근 도우미 ----------
        public static object Get(Dictionary<string, object> d, params string[] keys)
        {
            object v;
            foreach (var k in keys)
                if (d.TryGetValue(k, out v) && v != null) return v;
            return null;
        }

        public static double? Num(Dictionary<string, object> d, params string[] keys)
        {
            foreach (var k in keys)
            {
                object v;
                if (!d.TryGetValue(k, out v) || v == null) continue;
                if (v is double) { double x = (double)v; if (!double.IsNaN(x) && !double.IsInfinity(x)) return x; continue; }
                var str = v as string;
                double r;
                if (str != null && double.TryParse(str.Trim().TrimEnd('%'), NumberStyles.Float, CultureInfo.InvariantCulture, out r)) return r;
            }
            return null;
        }

        public static string Str(Dictionary<string, object> d, params string[] keys)
        {
            foreach (var k in keys)
            {
                object v;
                if (!d.TryGetValue(k, out v) || v == null) continue;
                if (v is string) { var s = ((string)v).Trim(); if (s.Length > 0) return s; continue; }
                if (v is double) return ((double)v).ToString(CultureInfo.InvariantCulture);
                if (v is bool) return ((bool)v) ? "true" : "false";
            }
            return null;
        }

        /// <summary>ISO 문자열·RFC822·유닉스 초/밀리초를 UTC DateTime 으로.</summary>
        public static DateTime? Time(Dictionary<string, object> d, params string[] keys)
        {
            foreach (var k in keys)
            {
                object v;
                if (!d.TryGetValue(k, out v) || v == null) continue;
                var t = ToTime(v);
                if (t.HasValue) return t;
            }
            return null;
        }

        static readonly string[] Rfc = {
            "ddd, dd MMM yyyy HH:mm:ss zzz", "ddd, d MMM yyyy HH:mm:ss zzz", "ddd, dd MMM yyyy HH:mm:ss 'GMT'",
            "ddd, d MMM yyyy HH:mm:ss 'GMT'", "ddd, dd MMM yyyy HH:mm:ss 'UTC'", "ddd, dd MMM yyyy HH:mm:ss 'Z'"
        };

        public static DateTime? ToTime(object v)
        {
            if (v == null) return null;
            if (v is double)
            {
                double x = (double)v;
                if (x > 1e14) x /= 1000.0;        // micro
                if (x > 1e11) return Epoch.AddMilliseconds(x);
                if (x > 1e8) return Epoch.AddSeconds(x);
                return null;
            }
            var s = v as string;
            if (string.IsNullOrWhiteSpace(s)) return null;
            s = s.Trim();
            double n;
            if (s.Length >= 9 && s.Length <= 16 && double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out n))
                return ToTime(n);
            DateTimeOffset dto;
            // 'Z'/오프셋이 없으면 UTC 로 간주
            if (DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AllowWhiteSpaces, out dto))
                return dto.UtcDateTime;
            var s2 = s.Replace(" +0000", " +00:00");
            foreach (var f in Rfc)
                if (DateTimeOffset.TryParseExact(s2, f, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out dto))
                    return dto.UtcDateTime;
            return null;
        }

        public static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);
    }
}
