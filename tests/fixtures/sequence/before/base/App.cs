namespace Demo;

public class Service
{
    public void Run()
    {
        Work();
    }

    private void Work()
    {
    }
}

public class App
{
    private readonly Service service = new Service();

    public void Main()
    {
        service.Run();
    }

    public void Quiet()
    {
        Plain();
    }

    private void Plain()
    {
    }
}
