using System;

namespace Demo;

public interface IStore
{
    void Save(Order order);
    Order Load(int id);
}

public class Customer
{
    public void Notify() { }
}

public class Order
{
    public Customer Owner { get; }
    public void Confirm() { }
}

public class Store : IStore
{
    public void Save(Order order) { order.Confirm(); }
    public Order Load(int id) { return new Order(); }
}

public class Util
{
    public static void Log(string message) { }
    public static Order Make() { return new Order(); }
}

public class Base
{
    protected void Helper() { }
    protected void Inherited() { }
}

public class Wrapper
{
    public Wrapper(Action action) { }
}

public class Rules : Base
{
    private readonly IStore store;
    private Customer customer;

    public Rules(IStore store) { this.store = store; }

    public void Rule1()
    {
        Helper();
        this.Helper();
        base.Inherited();
        Local();
        void Local() { }
    }

    public void Rule2(IEnumerable<Order> orders)
    {
        Order typed = new Order();
        typed.Confirm();
        var created = new Order();
        created.Confirm();
        var loaded = store.Load(1);
        loaded.Confirm();
        foreach (Order each in orders) { each.Confirm(); }
        if (typed is Customer pattern) { pattern.Notify(); }
    }

    public void Rule3(Order order) { order.Confirm(); }

    public void Rule4() { store.Save(null); }

    public void Rule4Property() { customer.Notify(); }

    public void Rule5() { Util.Log("x"); }

    public void Rule6(Order order) { order.Owner.Notify(); }

    public void Rule7() { store.Load(1).Confirm(); }

    public void Rule7Static() { Util.Make().Confirm(); }

    public void Rule8() { ArgumentNullException.ThrowIfNull(store); }

    public void Unresolved() { thing.Frob(); mystery.Do(); }

    public void Rule9() { Run(new Store()); }

    public void Rule9Plain(IStore given) { Run(given); }

    private void Run(IStore target) { target.Save(null); }

    public void Overloads()
    {
        Pick(1);
        Pick(1, 2);
        Same(1);
    }

    private void Pick(int a) { }
    private void Pick(int a, int b) { }
    private void Same(int a) { }
    private void Same(string a) { }

    public void Order1() { Outer(Inner()); }

    private int Outer(int value) { return value; }
    private int Inner() { return 1; }

    public void Lambdas(Order order)
    {
        Subscribe(() => { order.Confirm(); });
        Subscribe(x => x.Foo());
        Wrapper w = new(Handler);
        Subscribe(Handler);
        var other = new Wrapper(Handler);
    }

    private void Subscribe(Action action) { }
    private void Handler() { }
}
