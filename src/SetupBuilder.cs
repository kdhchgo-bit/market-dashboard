using System;
using System.Collections.Generic;
using System.Linq;

namespace MarketDash
{
    public static class SetupBuilder
    {
        // The user's elliott-wave-report library supplies the ATR/trade-math approach.
        // These intraday levels use confirmed swings and OHLCV volume nodes, without a claimed wave count.
        public static TradeSetup Build(Candidate candidate, IList<Bar> sourceBars, int minutes, DateTime evaluatedUtc)
        {
            var plan = new TradeSetup
            {
                Candidate = candidate, Minutes = minutes, EvaluatedUtc = evaluatedUtc,
                Status = "자료 확인", Reason = "완성 OHLCV 자료 확인 중",
                Method = "확정 스윙 지지·저항 + 분봉 대표가격 거래량 매물대(추정)",
                WaveNote = "elliott-wave-report의 ATR·분할 평단·R 계산을 적용. 다중 등급 엘리어트 카운트는 미확정."
            };
            if (candidate == null || sourceBars == null || (minutes != 5 && minutes != 15 && minutes != 60))
                return Fail(plan, "지원 시간봉은 5분·15분·1시간입니다.");

            bool coin = candidate.Market == "코인";
            var raw = sourceBars.Where(b => b != null && b.T >= evaluatedUtc.AddDays(-30)
                    && b.End <= evaluatedUtc && ValidBar(b))
                .GroupBy(b => b.T).Select(g => g.Last()).OrderBy(b => b.T).ToList();
            if (raw.Count == 0) return Fail(plan, "현재 시각 이전에 완성된 가격 봉이 없습니다.");
            var latest = raw[raw.Count - 1];
            plan.Current = latest.C;
            plan.QuoteUtc = latest.End;
            plan.Bars = Aggregate(raw, minutes, coin, evaluatedUtc);
            if (plan.Bars.Count == 0) return Fail(plan, "선택 시간봉의 연속된 완성 봉이 없습니다.");
            plan.AsOfUtc = plan.Bars[plan.Bars.Count - 1].T.AddMinutes(minutes);
            if (plan.Bars.Count < 20) return Fail(plan, "선택 시간봉 최소 20개 필요 · 현재 " + plan.Bars.Count + "개");
            plan.Atr = Atr(plan.Bars);
            if (!(plan.Atr > 0)) return Fail(plan, "유효한 변동폭을 계산할 수 없습니다.");

            plan.Pivots = Pivots(plan.Bars);
            var recent = plan.Bars.Skip(Math.Max(0, plan.Bars.Count - 320)).ToList();
            DateTime structureStart = recent[0].T;
            var structure = plan.Pivots.Where(p => p.Time >= structureStart).ToList();
            if (structure.Count < 2) return Fail(plan, "오른쪽 두 봉까지 확인한 스윙 지지·저항이 부족합니다.");
            var nodes = VolumeNodes(recent);
            double padding = Math.Max(plan.Atr * 0.15, plan.Current * 0.00001);
            var entryLevels = structure.Select(p => p.Price).Concat(nodes)
                .Where(p => candidate.IsShort ? p >= plan.Current : p <= plan.Current).ToList();
            if (entryLevels.Count == 0)
                return Fail(plan, candidate.IsShort ? "현재가 위의 진입 저항을 확인하지 못했습니다." : "현재가 아래의 진입 지지를 확인하지 못했습니다.");
            double anchor = candidate.IsShort ? entryLevels.Min() : entryLevels.Max();
            plan.EntryLow = anchor - padding;
            plan.EntryHigh = anchor + padding;
            if (plan.EntryLow <= 0) return Fail(plan, "진입 구간이 유효한 양수 가격을 벗어났습니다.");
            double middle = (plan.EntryLow + plan.EntryHigh) / 2;
            plan.Entries = candidate.IsShort
                ? new[] { plan.EntryLow, middle, plan.EntryHigh }
                : new[] { plan.EntryHigh, middle, plan.EntryLow };
            plan.Entry = 3.0 / plan.Entries.Sum(p => 1.0 / p);

            var invalidations = structure.Where(p => p.High == candidate.IsShort
                    && (candidate.IsShort ? p.Price >= plan.EntryLow : p.Price <= plan.EntryHigh)).ToList();
            if (invalidations.Count == 0)
                return Fail(plan, "진입 구간의 무효화를 판단할 확정 스윙이 없습니다.");
            double invalidation = candidate.IsShort ? invalidations.Min(p => p.Price) : invalidations.Max(p => p.Price);
            plan.Stop = candidate.IsShort
                ? Math.Max(invalidation + plan.Atr * 0.2, plan.EntryHigh + padding)
                : Math.Min(invalidation - plan.Atr * 0.2, plan.EntryLow - padding);
            double risk = Math.Abs(plan.Entry - plan.Stop);
            if (!(risk > 0) || plan.Stop <= 0) return Fail(plan, "손절가와 진입가의 순서가 유효하지 않습니다.");
            plan.RiskPct = 100.0 * risk / plan.Entry;

            // Targets come from observed opposing swings and volume concentrations.
            // Their risk multiples are calculated after the prices have been chosen.
            double beyond = candidate.IsShort ? Math.Min(plan.Current, plan.EntryLow) : Math.Max(plan.Current, plan.EntryHigh);
            var targetLevels = plan.Pivots.Where(p => p.High != candidate.IsShort).Select(p => p.Price)
                .Concat(nodes).Where(p => candidate.IsShort ? p < beyond - padding : p > beyond + padding);
            var ordered = candidate.IsShort ? targetLevels.OrderByDescending(p => p) : targetLevels.OrderBy(p => p);
            var targets = new List<double>();
            foreach (double price in ordered)
            {
                if (targets.Count > 0 && Math.Abs(price - targets[targets.Count - 1]) < plan.Atr * 0.25) continue;
                targets.Add(price);
                if (targets.Count == 3) break;
            }
            plan.Targets = targets.ToArray();
            if (targets.Count == 0) return Fail(plan, "현재가 앞의 확정 지지·저항 또는 매물대 목표가 없습니다.");
            plan.R = targets.Select(p => (candidate.IsShort ? plan.Entry - p : p - plan.Entry) / risk).ToArray();
            plan.HasLevels = true;
            UpdateState(plan, evaluatedUtc);
            return plan;
        }

        public static bool TryMoveLevel(TradeSetup plan, string kind, int index, double price, out string reason)
        {
            reason = null;
            if (plan == null || !plan.HasLevels)
            {
                reason = "조정할 진입·TP·SL 가격선이 준비되지 않았습니다.";
                return false;
            }
            if (!Finite(price) || price <= 0)
            {
                reason = "가격은 0보다 큰 유한한 숫자여야 합니다.";
                return false;
            }
            var edit = SetupLevelEdit.From(plan);
            switch (kind)
            {
                case "entry":
                    double halfWidth = (edit.EntryHigh - edit.EntryLow) / 2;
                    edit.EntryLow = price - halfWidth;
                    edit.EntryHigh = price + halfWidth;
                    break;
                case "stop":
                    edit.Stop = price;
                    break;
                case "target":
                    if (index < 0 || index >= edit.Targets.Length)
                    {
                        reason = "선택한 TP 가격선이 없습니다.";
                        return false;
                    }
                    edit.Targets[index] = price;
                    break;
                default:
                    reason = "진입·TP·SL 가격선만 조정할 수 있습니다.";
                    return false;
            }
            return ApplyLevels(plan, edit, out reason);
        }

        public static bool ApplyLevels(TradeSetup plan, SetupLevelEdit edit, out string reason)
        {
            reason = null;
            if (plan == null || plan.Candidate == null || edit == null)
            {
                reason = "적용할 셋업 또는 수동 가격 정보가 없습니다.";
                return false;
            }
            if (plan.Bars == null || plan.Bars.Count < 20 || !Finite(plan.Current) || plan.Current <= 0 || plan.QuoteUtc == DateTime.MinValue)
            {
                reason = "현재 가격과 최소 20개 완성 봉을 확인한 뒤 수동 가격을 적용할 수 있습니다.";
                return false;
            }
            if (!Finite(edit.EntryLow) || !Finite(edit.EntryHigh) || !Finite(edit.Stop)
                || edit.EntryLow <= 0 || edit.EntryHigh <= 0 || edit.Stop <= 0)
            {
                reason = "진입·SL 가격은 0보다 큰 유한한 숫자여야 합니다.";
                return false;
            }
            if (!(edit.EntryLow < edit.EntryHigh))
            {
                reason = "진입 구간 하단은 상단보다 낮아야 합니다.";
                return false;
            }
            if (edit.Targets == null || edit.Targets.Length < 1 || edit.Targets.Length > 3
                || edit.Targets.Any(p => !Finite(p) || p <= 0))
            {
                reason = "TP는 0보다 큰 유한한 가격 1~3개가 필요합니다.";
                return false;
            }
            var targets = (double[])edit.Targets.Clone();
            bool shortSide = plan.IsShort;
            if (shortSide ? edit.Stop <= edit.EntryHigh : edit.Stop >= edit.EntryLow)
            {
                reason = shortSide ? "숏 SL은 진입 구간 상단보다 높아야 합니다." : "롱 SL은 진입 구간 하단보다 낮아야 합니다.";
                return false;
            }
            double previous = shortSide ? edit.EntryLow : edit.EntryHigh;
            foreach (double target in targets)
            {
                if (shortSide ? target >= previous : target <= previous)
                {
                    reason = shortSide ? "숏 가격 순서는 TP3 < TP2 < TP1 < 진입 하단 < 진입 상단 < SL입니다."
                        : "롱 가격 순서는 SL < 진입 하단 < 진입 상단 < TP1 < TP2 < TP3입니다.";
                    return false;
                }
                previous = target;
            }
            double middle = edit.EntryLow + (edit.EntryHigh - edit.EntryLow) / 2;
            var entries = shortSide ? new[] { edit.EntryLow, middle, edit.EntryHigh } : new[] { edit.EntryHigh, middle, edit.EntryLow };
            double entry = 3.0 / entries.Sum(p => 1.0 / p);
            double risk = Math.Abs(entry - edit.Stop);
            double riskPct = risk / entry * 100;
            var ratios = targets.Select(p => (shortSide ? entry - p : p - entry) / risk).ToArray();
            if (!Finite(entry) || entry <= 0 || !Finite(risk) || risk <= 0 || !Finite(riskPct)
                || ratios.Any(r => !Finite(r) || r <= 0))
            {
                reason = "가격 차이가 너무 작거나 커서 유한한 손익비를 계산할 수 없습니다.";
                return false;
            }

            // Commit only after every validation/calculation succeeds.
            plan.EntryLow = edit.EntryLow; plan.EntryHigh = edit.EntryHigh; plan.Stop = edit.Stop;
            plan.Entries = entries; plan.Entry = entry; plan.Targets = targets;
            plan.RiskPct = riskPct; plan.R = ratios; plan.IsManual = true; plan.HasLevels = true;
            DateTime realNow = DateTime.UtcNow;
            UpdateState(plan, realNow > plan.EvaluatedUtc ? realNow : plan.EvaluatedUtc);
            return true;
        }

        static void UpdateState(TradeSetup plan, DateTime evaluatedUtc)
        {
            plan.CanEnter = false;
            string side = plan.IsShort ? "숏" : "롱";
            if (plan.QuoteUtc > evaluatedUtc || (evaluatedUtc - plan.QuoteUtc).TotalMinutes > Cfg.BarStaleMin)
            {
                plan.Status = side + " · 시세 확인 대기";
                plan.Reason = "마지막 완성 5분봉 " + Tz.Kst(plan.QuoteUtc, "HH:mm") + " KST · 현재 시세 갱신 필요";
            }
            else if (!plan.Candidate.Pass)
            {
                plan.Status = side + " · 진입 보류";
                plan.Reason = plan.Candidate.Reason ?? "시장·거래대금·옵션 조건 확인 대기";
            }
            else if (plan.IsShort ? plan.Current >= plan.Stop : plan.Current <= plan.Stop)
            {
                plan.Status = side + " · 셋업 무효";
                plan.Reason = "현재가가 구조 무효화 가격을 넘었습니다.";
            }
            else if (plan.IsShort ? plan.Current <= plan.Targets[0] : plan.Current >= plan.Targets[0])
            {
                plan.Status = side + " · TP1 이후";
                plan.Reason = "현재가가 TP1 가격을 지나갔습니다. 새 진입 계획 확인이 필요합니다.";
            }
            else if (plan.Current >= plan.EntryLow && plan.Current <= plan.EntryHigh)
            {
                plan.CanEnter = true;
                plan.Status = side + " · 진입 구간";
                plan.Reason = "현재가가 계획 진입 구간 안에 있습니다. 체결은 주문 확인이 필요합니다.";
            }
            else
            {
                plan.Status = side + (plan.IsShort ? " · 반등 대기" : " · 눌림 대기");
                plan.Reason = "지정가 구간 도달 대기 · 손익비는 계획 가격으로 계산한 수수료 전 값";
            }
            if (plan.Targets.Length < 3) plan.Reason += " · 추가 TP 매물대 미확인";
        }

        static TradeSetup Fail(TradeSetup plan, string reason)
        {
            plan.Status = (plan.IsShort ? "숏" : "롱") + " · 셋업 확인 대기";
            plan.Reason = reason;
            return plan;
        }

        static bool Finite(double value)
        {
            return !double.IsNaN(value) && !double.IsInfinity(value);
        }

        static bool ValidBar(Bar bar)
        {
            return Finite(bar.O) && Finite(bar.H) && Finite(bar.L) && Finite(bar.C)
                && Math.Min(Math.Min(bar.O, bar.C), bar.L) > 0
                && bar.H >= Math.Max(bar.O, bar.C) && bar.L <= Math.Min(bar.O, bar.C);
        }

        static List<Bar> Aggregate(List<Bar> raw, int minutes, bool coin, DateTime now)
        {
            int count = minutes / 5;
            var buckets = new Dictionary<DateTime, List<Bar>>();
            foreach (var bar in raw)
            {
                DateTime key;
                if (coin)
                {
                    key = bar.T.Date.AddMinutes((int)bar.T.TimeOfDay.TotalMinutes / minutes * minutes);
                }
                else
                {
                    var et = Tz.ToEt(bar.T);
                    double elapsed = (et.TimeOfDay - new TimeSpan(9, 30, 0)).TotalMinutes;
                    if (et.DayOfWeek == DayOfWeek.Saturday || et.DayOfWeek == DayOfWeek.Sunday || elapsed < 0 || elapsed >= 390) continue;
                    double start = Math.Floor(elapsed / minutes) * minutes;
                    if (start + minutes > 390) continue;
                    key = Tz.FromLocal(et.Date.AddHours(9.5).AddMinutes(start), Tz.ET);
                }
                List<Bar> list;
                if (!buckets.TryGetValue(key, out list)) buckets[key] = list = new List<Bar>();
                list.Add(bar);
            }
            var result = new List<Bar>();
            foreach (var pair in buckets.OrderBy(p => p.Key))
            {
                var bars = pair.Value.OrderBy(b => b.T).ToList();
                if (bars.Count != count || pair.Key.AddMinutes(minutes) > now) continue;
                bool complete = true;
                for (int i = 0; i < count; i++)
                    if (bars[i].T != pair.Key.AddMinutes(i * 5)) { complete = false; break; }
                if (!complete) continue;
                bool hasVolume = bars.All(b => b.V.HasValue && Finite(b.V.Value) && b.V.Value >= 0);
                bool hasQuote = bars.All(b => b.Quote.HasValue && Finite(b.Quote.Value) && b.Quote.Value >= 0);
                result.Add(new Bar
                {
                    T = pair.Key, O = bars[0].O, H = bars.Max(b => b.H), L = bars.Min(b => b.L), C = bars[bars.Count - 1].C,
                    V = hasVolume ? bars.Sum(b => b.V.Value) : (double?)null,
                    Quote = hasQuote ? bars.Sum(b => b.Quote.Value) : (double?)null
                });
            }
            return result;
        }

        static double Atr(List<Bar> bars)
        {
            const int n = 14;
            double value = 0;
            for (int i = 0; i < bars.Count; i++)
            {
                double tr = bars[i].H - bars[i].L;
                if (i > 0) tr = Math.Max(tr, Math.Max(Math.Abs(bars[i].H - bars[i - 1].C), Math.Abs(bars[i].L - bars[i - 1].C)));
                value = i < n ? value + tr / n : (value * (n - 1) + tr) / n;
            }
            return value;
        }

        static List<SetupPivot> Pivots(List<Bar> bars)
        {
            var points = new List<SetupPivot>();
            for (int i = 2; i < bars.Count - 2; i++)
            {
                var b = bars[i];
                bool high = b.H > bars[i - 1].H && b.H > bars[i - 2].H && b.H >= bars[i + 1].H && b.H >= bars[i + 2].H;
                bool low = b.L < bars[i - 1].L && b.L < bars[i - 2].L && b.L <= bars[i + 1].L && b.L <= bars[i + 2].L;
                if (high) points.Add(new SetupPivot { Time = b.T, Price = b.H, High = true });
                if (low) points.Add(new SetupPivot { Time = b.T, Price = b.L, High = false });
            }
            return points;
        }

        static List<double> VolumeNodes(List<Bar> bars)
        {
            const int bins = 48;
            double lo = bars.Min(b => b.L), hi = bars.Max(b => b.H);
            if (!(hi > lo)) return new List<double>();
            var volumes = new double[bins];
            var prices = new double[bins];
            double step = (hi - lo) / bins;
            foreach (var bar in bars)
            {
                double typical = (bar.H + bar.L + bar.C) / 3;
                double? volume = bar.V;
                if ((!volume.HasValue || volume <= 0) && bar.Quote.HasValue) volume = bar.Quote.Value / typical;
                if (!volume.HasValue || !Finite(volume.Value) || volume <= 0) continue;
                int bin = Math.Max(0, Math.Min(bins - 1, (int)((typical - lo) / step)));
                volumes[bin] += volume.Value;
                prices[bin] += typical * volume.Value;
            }
            var order = Enumerable.Range(0, bins).Where(i => volumes[i] > 0).OrderByDescending(i => volumes[i]).ToList();
            var nodes = new List<double>();
            foreach (int i in order)
            {
                if ((i > 0 && volumes[i] < volumes[i - 1]) || (i < bins - 1 && volumes[i] < volumes[i + 1])) continue;
                nodes.Add(prices[i] / volumes[i]);
                if (nodes.Count == 8) break;
            }
            return nodes;
        }
    }
}
