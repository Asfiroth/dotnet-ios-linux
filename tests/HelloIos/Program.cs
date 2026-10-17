using Foundation;
using UIKit;
using AotProbe;

namespace HelloIos;

public static class Program
{
    private static void Main(string[] args)
    {
        UIApplication.Main(args, null, typeof(AppDelegate));
    }
}

[Register("AppDelegate")]
public sealed class AppDelegate : UIApplicationDelegate
{
    public override UIWindow? Window { get; set; }

    public override bool FinishedLaunching(UIApplication application, NSDictionary? launchOptions)
    {
        var probeResult = DeviceAotProbe.Execute();
        Window = new UIWindow(UIScreen.MainScreen.Bounds)
        {
            RootViewController = new HelloViewController(probeResult)
        };
        Window.MakeKeyAndVisible();
        return true;
    }
}

public sealed class HelloViewController(AotProbeResult probeResult) : UIViewController
{
    public override void ViewDidLoad()
    {
        base.ViewDidLoad();
        View!.BackgroundColor = UIColor.SystemBackground;

        var label = new UILabel(View.Bounds)
        {
            Text = $"Hello from Linux-hosted .NET iOS\n"
                + $"AOT probe: {probeResult.GenericValue}; exception={probeResult.ExceptionCode}; "
                + $"pid={probeResult.ProcessId}; checksum={probeResult.SortedChecksum}",
            TextAlignment = UITextAlignment.Center,
            Lines = 0,
            AutoresizingMask = UIViewAutoresizing.FlexibleWidth | UIViewAutoresizing.FlexibleHeight
        };
        View.AddSubview(label);
    }
}
