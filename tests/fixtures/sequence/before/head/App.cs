namespace Demo;

public class Service
{
    public void Run()
    {
        Work();
        Extra();
    }

    private void Work()
    {
    }

    private void Extra()
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
        service.Run();
    }

    public void Added()
    {
        service.Run();
    }
}
