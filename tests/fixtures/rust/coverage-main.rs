fn covered(x: u32) -> u32 {
    if x > 10 {
        x - 1
    } else {
        x + 1
    }
}

fn never_called(x: u32) -> u32 {
    x * 2
}

fn main() {
    let n: u32 = std::env::args().count() as u32;
    println!("{}", covered(n + 20));
    if n > 100 {
        println!("{}", never_called(n));
    }
}
