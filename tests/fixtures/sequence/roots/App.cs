namespace Demo;

public interface IView
{
    void Show();
}

public class View : IView
{
    public void Show()
    {
        Format.Line();
    }
}

public static class Format
{
    public static void Line() { }
}

public static class Noise
{
    public static void Log() { }
}

public class Handler
{
    public void OnEvent()
    {
        Format.Line();
    }
}

public class Chain4
{
    public void Four()
    {
        Format.Line();
    }
}

public class Chain3
{
    private readonly Chain4 next = new Chain4();
    public void Three()
    {
        next.Four();
    }
}

public class Chain2
{
    private readonly Chain3 next = new Chain3();
    public void Two()
    {
        next.Three();
    }
}

public class Service
{
    public void Run()
    {
        Helper();
        Noise.Log();
        Noise.Log();
    }

    private void Helper()
    {
        Format.Line();
    }

    public void Loop()
    {
        Format.Line();
        Loop();
    }
}

public class App
{
    private readonly IView view = new View();
    private readonly Service service = new Service();
    private readonly Chain2 chain = new Chain2();

    public void Main()
    {
        view.Show();
        service.Run();
        service.Loop();
        chain.Two();
        unknown.Go();
        Bus.Subscribe(() => new Handler().OnEvent());
    }
}
