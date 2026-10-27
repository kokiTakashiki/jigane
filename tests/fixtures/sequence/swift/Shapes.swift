import SwiftUI

protocol Drawable: AnyObject {
    var size: Int { get }
    func draw(in rect: Int) -> Bool
}

class Base {}

class Canvas: Base, Drawable {
    var size: Int = 1
    let title: String?
    var doubled: Int { size * 2 }
    var explicit: Int {
        get { size }
        set { size = newValue }
    }

    init(title: String?) {
        self.title = title
    }

    func draw(in rect: Int) -> Bool {
        return rect > 0
    }

    func move(_ dx: Int, by step: Int, scale: Double = 1.0) {
        size += dx
    }

    struct Inner {
        func ping() {}
    }
}

extension Canvas {
    func extra() -> Int { size }
}

struct Wrapper: UIViewRepresentable {
    func makeUIView(context: Context) -> UIView { UIView() }
}

enum Mode: Int {
    case on, off
    func flip() -> Mode { .off }
}

actor Counter {
    var count = 0
}
