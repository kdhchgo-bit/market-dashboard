using System;
using System.Collections.Generic;

namespace MarketDash
{
    public class SetupPivot
    {
        public DateTime Time;
        public double Price;
        public bool High;
    }

    public class TradeSetup
    {
        public Candidate Candidate;
        public List<Bar> Bars = new List<Bar>();
        public List<SetupPivot> Pivots = new List<SetupPivot>();
        public int Minutes;
        public DateTime AsOfUtc, EvaluatedUtc, QuoteUtc;
        public bool HasLevels, CanEnter, IsManual;
        public string Status, Reason, Method, WaveNote;
        public double Current, EntryLow, EntryHigh, Entry, Stop, Atr, RiskPct;
        public double[] Entries = new double[0], Targets = new double[0], R = new double[0];
        public bool IsShort { get { return Candidate != null && Candidate.IsShort; } }
    }

    public class SetupLevelEdit
    {
        public double EntryLow, EntryHigh, Stop;
        public double[] Targets = new double[0];

        public static SetupLevelEdit From(TradeSetup plan)
        {
            if (plan == null) return null;
            return new SetupLevelEdit
            {
                EntryLow = plan.EntryLow, EntryHigh = plan.EntryHigh, Stop = plan.Stop,
                Targets = plan.Targets == null ? new double[0] : (double[])plan.Targets.Clone()
            };
        }
    }
}
