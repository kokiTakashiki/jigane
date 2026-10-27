namespace Demo;

public class Worker
{
    public void Step()
    {
        Done();
    }

    public void Done()
    {
    }
}

public class Runner
{
    private Worker worker;

    public void Run()
    {
        worker.Step();
    }

    public void Solo()
    {
        Idle();
        Idle();
        Idle();
    }

    public void Idle()
    {
    }
}
