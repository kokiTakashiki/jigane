namespace Demo;

public class Service
{
    public void Run()
    {
    }

    public void Old()
    {
    }
}

public class App
{
    private readonly Service service = new Service();

    public void Main()
    {
        service.Run();
        service.Old();
    }
}
