using System;
using System.Windows.Media;

namespace MarketDash
{
    public static class AppFont
    {
        public static readonly FontFamily Family = new FontFamily(
            new Uri(AppDomain.CurrentDomain.BaseDirectory, UriKind.Absolute), "./fonts/#Pretendard");
    }
}
