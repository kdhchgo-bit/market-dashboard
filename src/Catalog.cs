using System.Collections.Generic;

namespace MarketDash
{
    public enum Kind
    {
        UsBar,      // 미국 정규장 5분봉
        CoinBar,    // 코인 24시간 5분봉
        FxBar,      // 24/5 5분봉 (DXY)
        Daily,      // 일간 통계·관측값
        Calendar,   // 일정
        News,       // 헤드라인
        UsChain,    // 미국 옵션 체인
        CoinChain   // Deribit 옵션 체인
    }

    public class Source
    {
        public int No;
        public string Tag;      // data/<Tag>
        public string Name;     // 한국어 설명
        public Kind Kind;
        public bool IsOption { get { return No >= 71; } }
        public Source(int no, string tag, string name, Kind k) { No = no; Tag = tag; Name = name; Kind = k; }
    }

    public static class Catalog
    {
        public static readonly List<Source> All = new List<Source>
        {
            new Source(1,"GLD","금 ETF",Kind.UsBar), new Source(2,"USO","원유 ETF",Kind.UsBar),
            new Source(3,"CPER","구리 ETF",Kind.UsBar), new Source(4,"UUP","달러 강세 ETF",Kind.UsBar),
            new Source(5,"SPY","S&P500 ETF",Kind.UsBar), new Source(6,"QQQ","나스닥100 ETF",Kind.UsBar),
            new Source(7,"XLK","기술 업종",Kind.UsBar), new Source(8,"XLE","에너지 업종",Kind.UsBar),
            new Source(9,"XLF","금융 업종",Kind.UsBar), new Source(10,"XLV","헬스케어 업종",Kind.UsBar),
            new Source(11,"NVDA","엔비디아",Kind.UsBar), new Source(12,"MSFT","마이크로소프트",Kind.UsBar),
            new Source(13,"XOM","엑슨모빌",Kind.UsBar), new Source(14,"CVX","셰브론",Kind.UsBar),
            new Source(15,"JPM","JP모건",Kind.UsBar), new Source(16,"BAC","뱅크오브아메리카",Kind.UsBar),
            new Source(17,"LLY","일라이릴리",Kind.UsBar), new Source(18,"JNJ","존슨앤존슨",Kind.UsBar),
            new Source(19,"BTC","비트코인",Kind.CoinBar), new Source(20,"ETH","이더리움",Kind.CoinBar),
            new Source(21,"SOL","솔라나",Kind.CoinBar), new Source(22,"AVAX","아발란체",Kind.CoinBar),
            new Source(23,"AAVE","에이브",Kind.CoinBar), new Source(24,"UNI","유니스왑",Kind.CoinBar),
            new Source(25,"CRV","커브",Kind.CoinBar), new Source(26,"US_YIELDS","미 국채 2Y·10Y",Kind.Daily),
            new Source(27,"BLS","CPI·고용 일정",Kind.Calendar), new Source(28,"FOMC","FOMC 일정",Kind.Calendar),
            new Source(29,"ECB","ECB 금리 일정",Kind.Calendar), new Source(30,"BBC_WORLD","BBC 국제 헤드라인",Kind.News),
            new Source(31,"XLI","산업재 업종",Kind.UsBar), new Source(32,"XLU","유틸리티 업종",Kind.UsBar),
            new Source(33,"XLP","필수소비재 업종",Kind.UsBar), new Source(34,"XLY","임의소비재 업종",Kind.UsBar),
            new Source(35,"XLB","소재 업종",Kind.UsBar), new Source(36,"XLRE","부동산 업종",Kind.UsBar),
            new Source(37,"XLC","커뮤니케이션 업종",Kind.UsBar), new Source(38,"DIA","다우 ETF",Kind.UsBar),
            new Source(39,"IWM","러셀2000 ETF",Kind.UsBar), new Source(40,"TLT","장기채 ETF",Kind.UsBar),
            new Source(41,"IEF","중기채 ETF",Kind.UsBar), new Source(42,"HYG","하이일드 ETF",Kind.UsBar),
            new Source(43,"VXX","VIX 선물 ETN",Kind.UsBar), new Source(44,"EEM","신흥국 ETF",Kind.UsBar),
            new Source(45,"EFA","선진국 ETF",Kind.UsBar), new Source(46,"SLV","은 ETF",Kind.UsBar),
            new Source(47,"UNG","천연가스 ETF",Kind.UsBar), new Source(48,"DBA","농산물 ETF",Kind.UsBar),
            new Source(49,"FXE","유로 ETF",Kind.UsBar), new Source(50,"FXY","엔화 ETF",Kind.UsBar),
            new Source(51,"AAPL","애플",Kind.UsBar), new Source(52,"AMZN","아마존",Kind.UsBar),
            new Source(53,"GOOGL","알파벳",Kind.UsBar), new Source(54,"META","메타",Kind.UsBar),
            new Source(55,"TSLA","테슬라",Kind.UsBar), new Source(56,"AMD","AMD",Kind.UsBar),
            new Source(57,"AVGO","브로드컴",Kind.UsBar), new Source(58,"WMT","월마트",Kind.UsBar),
            new Source(59,"UNH","유나이티드헬스",Kind.UsBar), new Source(60,"MA","마스터카드",Kind.UsBar),
            new Source(61,"XRP","리플",Kind.CoinBar), new Source(62,"DOGE","도지",Kind.CoinBar),
            new Source(63,"ADA","카르다노",Kind.CoinBar), new Source(64,"LINK","체인링크",Kind.CoinBar),
            new Source(65,"ARB","아비트럼",Kind.CoinBar), new Source(66,"OP","옵티미즘",Kind.CoinBar),
            new Source(67,"FRED","FRED 금리·물가",Kind.Daily), new Source(68,"DXY","달러 인덱스",Kind.FxBar),
            new Source(69,"VIX","VIX 지수",Kind.UsBar), new Source(70,"COINDESK","코인데스크 헤드라인",Kind.News),
            new Source(71,"PCR_TOTAL","전체 풋/콜",Kind.Daily), new Source(72,"PCR_EQUITY","개별주 풋/콜",Kind.Daily),
            new Source(73,"PCR_INDEX","지수 풋/콜",Kind.Daily), new Source(74,"VIX_TERM","VIX 기간구조",Kind.Daily),
            new Source(75,"SKEW","CBOE SKEW",Kind.Daily), new Source(76,"SPX_CHAIN","SPX 체인",Kind.UsChain),
            new Source(77,"VIX_OPTIONS","VIX 옵션",Kind.UsChain), new Source(78,"RUT_CHAIN","RUT 체인",Kind.UsChain),
            new Source(79,"SPY_CHAIN","SPY 체인",Kind.UsChain), new Source(80,"QQQ_CHAIN","QQQ 체인",Kind.UsChain),
            new Source(81,"DIA_CHAIN","DIA 체인",Kind.UsChain), new Source(82,"IWM_CHAIN","IWM 체인",Kind.UsChain),
            new Source(83,"TLT_CHAIN","TLT 체인",Kind.UsChain), new Source(84,"GLD_CHAIN","GLD 체인",Kind.UsChain),
            new Source(85,"USO_CHAIN","USO 체인",Kind.UsChain), new Source(86,"XLK_CHAIN","XLK 체인",Kind.UsChain),
            new Source(87,"XLF_CHAIN","XLF 체인",Kind.UsChain), new Source(88,"XLV_CHAIN","XLV 체인",Kind.UsChain),
            new Source(89,"EEM_CHAIN","EEM 체인",Kind.UsChain), new Source(90,"SLV_CHAIN","SLV 체인",Kind.UsChain),
            new Source(91,"NVDA_CHAIN","NVDA 체인",Kind.UsChain), new Source(92,"MSFT_CHAIN","MSFT 체인",Kind.UsChain),
            new Source(93,"AAPL_CHAIN","AAPL 체인",Kind.UsChain), new Source(94,"AMZN_CHAIN","AMZN 체인",Kind.UsChain),
            new Source(95,"GOOGL_CHAIN","GOOGL 체인",Kind.UsChain), new Source(96,"META_CHAIN","META 체인",Kind.UsChain),
            new Source(97,"TSLA_CHAIN","TSLA 체인",Kind.UsChain), new Source(98,"AMD_CHAIN","AMD 체인",Kind.UsChain),
            new Source(99,"VVIX","VVIX",Kind.Daily), new Source(100,"OCC_EXPIRY","OCC 만기 일정",Kind.Calendar),
            new Source(101,"BTC_DERIBIT","BTC Deribit 체인",Kind.CoinChain), new Source(102,"ETH_DERIBIT","ETH Deribit 체인",Kind.CoinChain),
        };

        public static readonly Dictionary<string, Source> ByTag = MakeByTag();
        static Dictionary<string, Source> MakeByTag()
        {
            var d = new Dictionary<string, Source>();
            foreach (var s in All) d[s.Tag] = s;
            return d;
        }

        // ---------- 그룹 정의 ----------
        public static readonly string[] Sectors = { "XLK", "XLE", "XLF", "XLV", "XLI", "XLU", "XLP", "XLY", "XLB", "XLRE", "XLC" };
        public static readonly Dictionary<string, string> SectorName = new Dictionary<string, string>
        {
            {"XLK","기술"},{"XLE","에너지"},{"XLF","금융"},{"XLV","헬스케어"},{"XLI","산업재"},{"XLU","유틸리티"},
            {"XLP","필수소비재"},{"XLY","임의소비재"},{"XLB","소재"},{"XLRE","부동산"},{"XLC","커뮤니케이션"}
        };
        public static readonly Dictionary<string, string> StockSector = new Dictionary<string, string>
        {
            {"NVDA","XLK"},{"MSFT","XLK"},{"AAPL","XLK"},{"AMD","XLK"},{"AVGO","XLK"},
            {"XOM","XLE"},{"CVX","XLE"},{"JPM","XLF"},{"BAC","XLF"},{"MA","XLF"},
            {"LLY","XLV"},{"JNJ","XLV"},{"UNH","XLV"},{"AMZN","XLY"},{"TSLA","XLY"},
            {"GOOGL","XLC"},{"META","XLC"},{"WMT","XLP"}
        };
        public static readonly string[] Alts = { "ETH", "SOL", "AVAX", "ADA", "AAVE", "UNI", "CRV", "ARB", "OP", "XRP", "DOGE", "LINK" };
        public static readonly string[] Themes = { "L1", "DeFi", "L2", "XRP", "DOGE", "LINK" };
        public static readonly Dictionary<string, string[]> ThemeMembers = new Dictionary<string, string[]>
        {
            {"L1", new[]{"ETH","SOL","AVAX","ADA"}}, {"DeFi", new[]{"AAVE","UNI","CRV"}}, {"L2", new[]{"ARB","OP"}},
            {"XRP", new[]{"XRP"}}, {"DOGE", new[]{"DOGE"}}, {"LINK", new[]{"LINK"}}
        };
        public static readonly Dictionary<string, string> ThemeName = new Dictionary<string, string>
        {
            {"L1","레이어1"},{"DeFi","디파이"},{"L2","레이어2"},{"XRP","리플"},{"DOGE","도지"},{"LINK","체인링크"}
        };
        public static string CoinTheme(string c)
        {
            foreach (var kv in ThemeMembers) if (System.Array.IndexOf(kv.Value, c) >= 0) return kv.Key;
            return null;
        }

        /// <summary>미국 주식의 지정 체인: 자기 체인 → 업종 체인 → SPY.</summary>
        public static void StockChain(string stock, out string chainTag, out string relation)
        {
            if (ByTag.ContainsKey(stock + "_CHAIN")) { chainTag = stock + "_CHAIN"; relation = "직접"; return; }
            string sec;
            if (StockSector.TryGetValue(stock, out sec) && ByTag.ContainsKey(sec + "_CHAIN")) { chainTag = sec + "_CHAIN"; relation = "업종 대용"; return; }
            chainTag = "SPY_CHAIN"; relation = "시장 대용";
        }
        public static void CoinChain(string coin, out string chainTag, out string relation)
        {
            if (coin == "ETH") { chainTag = "ETH_DERIBIT"; relation = "직접"; return; }
            chainTag = "BTC_DERIBIT"; relation = "시장 대용";
        }
    }
}
