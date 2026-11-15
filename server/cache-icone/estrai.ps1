
param([string]$elenco)
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System; using System.Drawing; using System.Drawing.Imaging; using System.Runtime.InteropServices;
public static class Icona {
  [ComImport, Guid("bcc18b79-ba16-442f-80c4-8a59c30c463b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IShellItemImageFactory { void GetImage(SIZE s, int flags, out IntPtr hbm); }
  [StructLayout(LayoutKind.Sequential)] struct SIZE { public int cx; public int cy; }
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string p, IntPtr bc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
      [MarshalAs(UnmanagedType.Interface)] out IShellItemImageFactory f);
  [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr h);
  public static void Salva(string sorgente, string png) {
    IShellItemImageFactory f;
    SHCreateItemFromParsingName(sorgente, IntPtr.Zero, typeof(IShellItemImageFactory).GUID, out f);
    IntPtr hbm; SIZE s; s.cx = 128; s.cy = 128;
    f.GetImage(s, 0x1 | 0x4, out hbm);   // BIGGERSIZEOK | ICONONLY
    try {
      using (Bitmap b = Image.FromHbitmap(hbm)) {
        Rectangle r = new Rectangle(0, 0, b.Width, b.Height);
        BitmapData d = b.LockBits(r, ImageLockMode.ReadOnly, b.PixelFormat);
        using (Bitmap a = new Bitmap(d.Width, d.Height, d.Stride, PixelFormat.Format32bppArgb, d.Scan0))
        using (Bitmap c = new Bitmap(a)) { c.Save(png, ImageFormat.Png); }
        b.UnlockBits(d);
      }
    } finally { DeleteObject(hbm); }
  }
}
'@
foreach ($riga in Get-Content -LiteralPath $elenco -Encoding UTF8) {
  $s, $d = $riga -split "`t", 2
  try { [Icona]::Salva($s, $d) } catch { }
}
