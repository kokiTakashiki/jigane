package com.example.inventory;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Predicate;

/**
 * Inventory service used as a parsing fixture.
 */
public class Inventory {
    public static final int MAX_ITEMS = 1000, MIN_ITEMS = 0;
    private final Map<String, Item> items = new HashMap<>();
    protected int revision;
    int cacheHits;

    public Inventory() {
        this(0);
    }

    Inventory(int revision) {
        this.revision = revision;
    }

    public enum Status {
        ACTIVE, DISCONTINUED("old") {
            @Override
            String label() {
                return "gone";
            }
        };

        private final String note;

        Status() {
            this("");
        }

        Status(String note) {
            this.note = note;
        }

        String label() {
            return note;
        }
    }

    public interface Listener {
        int PRIORITY = 5;

        void onChange(Item item);

        default void onClear() {
        }
    }

    public record Item(String sku, int quantity) {
        public Item {
            if (quantity < 0) {
                throw new IllegalArgumentException("negative");
            }
        }

        public Item withQuantity(int quantity) {
            return new Item(sku, quantity);
        }
    }

    @FunctionalInterface
    public @interface Marker {
        String value() default "";
    }

    public void add(Item item) { items.put(item.sku(), item); revision++; }

    public Optional<Item> find(String sku) {
        return Optional.ofNullable(items.get(sku));
    }

    public List<Item> filter(
        Predicate<Item> predicate,
        int limit
    ) {
        List<Item> result = new ArrayList<>();
        for (Item item : items.values()) {
            if (predicate.test(item) && result.size() < limit) {
                result.add(item);
            }
        }
        return result;
    }

    @Deprecated
    @SuppressWarnings("unchecked")
    public <T extends Comparable<T>> T max(List<T> values) throws IllegalStateException {
        Runnable r = () -> { revision++; };
        r.run();
        Object o = new Object() {
            @Override
            public String toString() {
                return "anon";
            }
        };
        T best = null;
        for (T value : values) {
            if (best == null || value.compareTo(best) > 0) {
                best = value;
            }
        }
        return best;
    }

    public int count() { return items.size(); }
    public int count(Predicate<Item> predicate) { return filter(predicate, MAX_ITEMS).size(); }
    private void touch() { revision++; }
    abstract static class Base { abstract void run(); }

    public static class Handler1 implements Listener {
        private int seen1;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen1++;
            if (item.quantity() > 1) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen1;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler2 implements Listener {
        private int seen2;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen2++;
            if (item.quantity() > 2) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen2;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler3 implements Listener {
        private int seen3;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen3++;
            if (item.quantity() > 3) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen3;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler4 implements Listener {
        private int seen4;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen4++;
            if (item.quantity() > 4) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen4;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler5 implements Listener {
        private int seen5;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen5++;
            if (item.quantity() > 5) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen5;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler6 implements Listener {
        private int seen6;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen6++;
            if (item.quantity() > 6) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen6;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler7 implements Listener {
        private int seen7;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen7++;
            if (item.quantity() > 7) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen7;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }

    public static class Handler8 implements Listener {
        private int seen8;
        private final List<String> log = new ArrayList<>();

        @Override
        public void onChange(Item item) {
            seen8++;
            if (item.quantity() > 8) {
                log.add("big " + item.sku());
            } else if (item.quantity() == 0) {
                log.add("empty " + item.sku());
            } else {
                log.add("small " + item.sku());
            }
        }

        public int total(List<Item> items) {
            int sum = 0;
            for (Item item : items) {
                switch (item.sku()) {
                    case "a":
                        sum += item.quantity();
                        break;
                    default:
                        sum += 1;
                }
            }
            return sum + seen8;
        }

        private String describe(Item item) {
            return String.format("%s:%d", item.sku(), item.quantity());
        }
    }
}
