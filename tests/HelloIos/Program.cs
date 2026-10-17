using Foundation;
using UIKit;

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
        Window = new UIWindow(UIScreen.MainScreen.Bounds)
        {
            RootViewController = new HelloViewController()
        };
        Window.MakeKeyAndVisible();
        return true;
    }
}

public sealed class HelloViewController : UIViewController
{
    public override void ViewDidLoad()
    {
        base.ViewDidLoad();
        View!.BackgroundColor = UIColor.SystemBackground;

        var label = new UILabel(View.Bounds)
        {
            Text = "Hello from Linux-hosted .NET iOS",
            TextAlignment = UITextAlignment.Center,
            Lines = 0,
            AutoresizingMask = UIViewAutoresizing.FlexibleWidth | UIViewAutoresizing.FlexibleHeight
        };
        View.AddSubview(label);
    }
}
