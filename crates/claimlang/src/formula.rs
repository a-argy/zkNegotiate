//! Tier 2, "formula": a small JavaScript-like expression language.
//!
//! Field access, arithmetic, comparisons, `&&`/`||`/`!`, list functions with
//! lambdas (`offers.filter(o => o.amount > 1e6).length >= 2`) and dates. No
//! loops, assignment or recursion, so every formula terminates; evaluation is
//! also metered. The result must be a boolean.

use crate::value::{bail, parse_date, Error, Value};

// ---------------------------------------------------------------- lexer

#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Num(f64),
    Str(String),
    Ident(String),
    Punct(&'static str),
}

const PUNCT: [&str; 22] = [
    "===", "!==", "=>", "&&", "||", "==", "!=", "<=", ">=", "<", ">", "!", "+", "-", "*", "/", "%",
    "(", ")", "[", "]", ",",
];

fn lex(src: &str) -> Result<Vec<Tok>, Error> {
    let chars: Vec<char> = src.chars().collect();
    let mut toks = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c.is_whitespace() {
            i += 1;
        } else if c.is_ascii_digit() || (c == '.' && chars.get(i + 1).is_some_and(|d| d.is_ascii_digit())) {
            let start = i;
            while i < chars.len() && (chars[i].is_ascii_digit() || chars[i] == '_' || chars[i] == '.') {
                i += 1;
            }
            if i < chars.len() && (chars[i] == 'e' || chars[i] == 'E') {
                i += 1;
                if i < chars.len() && (chars[i] == '+' || chars[i] == '-') {
                    i += 1;
                }
                while i < chars.len() && chars[i].is_ascii_digit() {
                    i += 1;
                }
            }
            let text: String = chars[start..i].iter().filter(|&&c| c != '_').collect();
            let n: f64 = text.parse().map_err(|_| Error(format!("bad number {text:?}")))?;
            toks.push(Tok::Num(n));
        } else if c == '"' || c == '\'' {
            let mut s = String::new();
            i += 1;
            loop {
                match chars.get(i) {
                    None => bail!("unterminated string"),
                    Some(&q) if q == c => break,
                    Some('\\') => {
                        i += 1;
                        match chars.get(i) {
                            Some('n') => s.push('\n'),
                            Some(&e) => s.push(e),
                            None => bail!("unterminated string"),
                        }
                    }
                    Some(&ch) => s.push(ch),
                }
                i += 1;
            }
            i += 1;
            toks.push(Tok::Str(s));
        } else if c.is_alphabetic() || c == '_' || c == '$' {
            let start = i;
            while i < chars.len() && (chars[i].is_alphanumeric() || chars[i] == '_' || chars[i] == '$') {
                i += 1;
            }
            toks.push(Tok::Ident(chars[start..i].iter().collect()));
        } else if c == '.' {
            toks.push(Tok::Punct("."));
            i += 1;
        } else {
            let rest: String = chars[i..chars.len().min(i + 3)].iter().collect();
            let Some(p) = PUNCT.iter().find(|p| rest.starts_with(**p)) else {
                bail!("unexpected character {c:?}");
            };
            toks.push(Tok::Punct(p));
            i += p.chars().count();
        }
    }
    Ok(toks)
}

// ---------------------------------------------------------------- AST

#[derive(Clone, Debug, PartialEq)]
pub enum Expr {
    Lit(Value),
    Var(String),
    Field(Box<Expr>, String),
    Index(Box<Expr>, Box<Expr>),
    Not(Box<Expr>),
    Neg(Box<Expr>),
    Binary(BinOp, Box<Expr>, Box<Expr>),
    /// `f(args)` or, with the receiver as the first argument, `x.f(args)`.
    Call(String, Vec<Arg>),
    List(Vec<Expr>),
}

#[derive(Clone, Debug, PartialEq)]
pub enum Arg {
    Expr(Expr),
    Lambda(String, Expr),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum BinOp {
    Or,
    And,
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    Add,
    Sub,
    Mul,
    Div,
    Rem,
}

// ---------------------------------------------------------------- parser

pub fn parse(src: &str) -> Result<Expr, Error> {
    let toks = lex(src)?;
    if toks.is_empty() {
        bail!("empty formula");
    }
    let mut p = Parser { toks, pos: 0, depth: 0 };
    let e = p.expr()?;
    if p.pos != p.toks.len() {
        bail!("unexpected {} after end of formula", p.describe());
    }
    Ok(e)
}

struct Parser {
    toks: Vec<Tok>,
    pos: usize,
    depth: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Tok> {
        self.toks.get(self.pos)
    }

    fn peek_at(&self, k: usize) -> Option<&Tok> {
        self.toks.get(self.pos + k)
    }

    fn describe(&self) -> String {
        match self.peek() {
            None => "end of formula".into(),
            Some(Tok::Num(n)) => format!("number {n}"),
            Some(Tok::Str(s)) => format!("string {s:?}"),
            Some(Tok::Ident(s)) => format!("{s:?}"),
            Some(Tok::Punct(p)) => format!("{p:?}"),
        }
    }

    fn eat(&mut self, p: &str) -> bool {
        if matches!(self.peek(), Some(Tok::Punct(q)) if *q == p) {
            self.pos += 1;
            true
        } else {
            false
        }
    }

    fn eat_word(&mut self, w: &str) -> bool {
        if matches!(self.peek(), Some(Tok::Ident(q)) if q == w) {
            self.pos += 1;
            true
        } else {
            false
        }
    }

    fn expect(&mut self, p: &str) -> Result<(), Error> {
        if self.eat(p) {
            Ok(())
        } else {
            bail!("expected {p:?}, found {}", self.describe())
        }
    }

    fn ident(&mut self) -> Result<String, Error> {
        match self.peek() {
            Some(Tok::Ident(s)) => {
                let s = s.clone();
                self.pos += 1;
                Ok(s)
            }
            _ => bail!("expected a name, found {}", self.describe()),
        }
    }

    fn expr(&mut self) -> Result<Expr, Error> {
        self.depth += 1;
        if self.depth > 64 {
            bail!("formula nested too deeply");
        }
        let e = self.or();
        self.depth -= 1;
        e
    }

    fn or(&mut self) -> Result<Expr, Error> {
        let mut l = self.and()?;
        while self.eat("||") || self.eat_word("or") {
            l = Expr::Binary(BinOp::Or, Box::new(l), Box::new(self.and()?));
        }
        Ok(l)
    }

    fn and(&mut self) -> Result<Expr, Error> {
        let mut l = self.cmp()?;
        while self.eat("&&") || self.eat_word("and") {
            l = Expr::Binary(BinOp::And, Box::new(l), Box::new(self.cmp()?));
        }
        Ok(l)
    }

    fn cmp(&mut self) -> Result<Expr, Error> {
        let l = self.add()?;
        let op = match self.peek() {
            Some(Tok::Punct("==" | "===")) => BinOp::Eq,
            Some(Tok::Punct("!=" | "!==")) => BinOp::Ne,
            Some(Tok::Punct("<")) => BinOp::Lt,
            Some(Tok::Punct("<=")) => BinOp::Le,
            Some(Tok::Punct(">")) => BinOp::Gt,
            Some(Tok::Punct(">=")) => BinOp::Ge,
            _ => return Ok(l),
        };
        self.pos += 1;
        let r = self.add()?;
        if matches!(self.peek(), Some(Tok::Punct("==" | "===" | "!=" | "!==" | "<" | "<=" | ">" | ">="))) {
            bail!("chained comparisons are ambiguous; join them with &&");
        }
        Ok(Expr::Binary(op, Box::new(l), Box::new(r)))
    }

    fn add(&mut self) -> Result<Expr, Error> {
        let mut l = self.mul()?;
        loop {
            let op = if self.eat("+") {
                BinOp::Add
            } else if self.eat("-") {
                BinOp::Sub
            } else {
                return Ok(l);
            };
            l = Expr::Binary(op, Box::new(l), Box::new(self.mul()?));
        }
    }

    fn mul(&mut self) -> Result<Expr, Error> {
        let mut l = self.unary()?;
        loop {
            let op = if self.eat("*") {
                BinOp::Mul
            } else if self.eat("/") {
                BinOp::Div
            } else if self.eat("%") {
                BinOp::Rem
            } else {
                return Ok(l);
            };
            l = Expr::Binary(op, Box::new(l), Box::new(self.unary()?));
        }
    }

    fn unary(&mut self) -> Result<Expr, Error> {
        if self.eat("!") || self.eat_word("not") {
            return Ok(Expr::Not(Box::new(self.unary()?)));
        }
        if self.eat("-") {
            return Ok(Expr::Neg(Box::new(self.unary()?)));
        }
        self.postfix()
    }

    fn postfix(&mut self) -> Result<Expr, Error> {
        let mut e = self.primary()?;
        loop {
            if self.eat(".") {
                let name = self.ident()?;
                if self.eat("(") {
                    let mut args = vec![Arg::Expr(e)];
                    args.extend(self.args()?);
                    e = Expr::Call(name, args);
                } else {
                    e = Expr::Field(Box::new(e), name);
                }
            } else if self.eat("[") {
                let i = self.expr()?;
                self.expect("]")?;
                e = Expr::Index(Box::new(e), Box::new(i));
            } else {
                return Ok(e);
            }
        }
    }

    /// Arguments after `(`, through the closing `)`.
    fn args(&mut self) -> Result<Vec<Arg>, Error> {
        let mut args = Vec::new();
        if self.eat(")") {
            return Ok(args);
        }
        loop {
            args.push(self.arg()?);
            if self.eat(")") {
                return Ok(args);
            }
            self.expect(",")?;
        }
    }

    fn arg(&mut self) -> Result<Arg, Error> {
        // `x => body` or `(x) => body`
        if let (Some(Tok::Ident(x)), Some(Tok::Punct("=>"))) = (self.peek(), self.peek_at(1)) {
            let x = x.clone();
            self.pos += 2;
            return Ok(Arg::Lambda(x, self.expr()?));
        }
        if let (Some(Tok::Punct("(")), Some(Tok::Ident(x)), Some(Tok::Punct(")")), Some(Tok::Punct("=>"))) =
            (self.peek(), self.peek_at(1), self.peek_at(2), self.peek_at(3))
        {
            let x = x.clone();
            self.pos += 4;
            return Ok(Arg::Lambda(x, self.expr()?));
        }
        Ok(Arg::Expr(self.expr()?))
    }

    fn primary(&mut self) -> Result<Expr, Error> {
        let tok = self.peek().cloned();
        match tok {
            Some(Tok::Num(n)) => {
                self.pos += 1;
                Ok(Expr::Lit(Value::Num(n)))
            }
            Some(Tok::Str(s)) => {
                self.pos += 1;
                Ok(Expr::Lit(Value::Str(s)))
            }
            Some(Tok::Punct("(")) => {
                self.pos += 1;
                let e = self.expr()?;
                self.expect(")")?;
                Ok(e)
            }
            Some(Tok::Punct("[")) => {
                self.pos += 1;
                let mut items = Vec::new();
                if !self.eat("]") {
                    loop {
                        items.push(self.expr()?);
                        if self.eat("]") {
                            break;
                        }
                        self.expect(",")?;
                    }
                }
                Ok(Expr::List(items))
            }
            Some(Tok::Ident(name)) => {
                self.pos += 1;
                match name.as_str() {
                    "true" => return Ok(Expr::Lit(Value::Bool(true))),
                    "false" => return Ok(Expr::Lit(Value::Bool(false))),
                    "null" => return Ok(Expr::Lit(Value::Null)),
                    _ => {}
                }
                if self.eat("(") {
                    Ok(Expr::Call(name, self.args()?))
                } else {
                    Ok(Expr::Var(name))
                }
            }
            _ => bail!("unexpected {}", self.describe()),
        }
    }
}

// ---------------------------------------------------------------- evaluator

/// Upper bound on evaluation steps, so a formula over large lists cannot run
/// unboundedly inside the zkVM.
const FUEL: u64 = 1_000_000;

pub struct Evaluator<'a> {
    /// Root bindings (document labels, `now`), then lambda parameters.
    scope: Vec<(String, &'a Value)>,
    locals: Vec<(String, Value)>,
    fuel: u64,
}

pub fn evaluate(expr: &Expr, bindings: &[(String, Value)]) -> Result<bool, Error> {
    let mut ev = Evaluator { scope: bindings.iter().map(|(k, v)| (k.clone(), v)).collect(), locals: Vec::new(), fuel: FUEL };
    match ev.eval(expr)? {
        Value::Bool(b) => Ok(b),
        v => bail!("a claim must be true/false, but this formula produces a {}", v.type_name()),
    }
}

fn num(v: &Value, what: &str) -> Result<f64, Error> {
    match v {
        Value::Num(n) => Ok(*n),
        _ => bail!("{what} needs a number, got {}", v.type_name()),
    }
}

fn boolean(v: &Value, what: &str) -> Result<bool, Error> {
    match v {
        Value::Bool(b) => Ok(*b),
        _ => bail!("{what} needs true/false, got {}", v.type_name()),
    }
}

impl<'a> Evaluator<'a> {
    fn lookup(&self, name: &str) -> Result<Value, Error> {
        if let Some((_, v)) = self.locals.iter().rev().find(|(k, _)| k == name) {
            return Ok(v.clone());
        }
        if let Some((_, v)) = self.scope.iter().find(|(k, _)| k == name) {
            return Ok((*v).clone());
        }
        let known: Vec<&str> = self.scope.iter().map(|(k, _)| k.as_str()).collect();
        bail!("unknown name {name:?} (available: {})", known.join(", "))
    }

    fn eval(&mut self, e: &Expr) -> Result<Value, Error> {
        if self.fuel == 0 {
            bail!("formula exceeded its evaluation budget");
        }
        self.fuel -= 1;
        Ok(match e {
            Expr::Lit(v) => v.clone(),
            Expr::Var(name) => self.lookup(name)?,
            Expr::List(items) => Value::List(items.iter().map(|i| self.eval(i)).collect::<Result<_, _>>()?),
            Expr::Field(base, name) => {
                let base = self.eval(base)?;
                match (&base, name.as_str()) {
                    (Value::Map(m), _) => match m.get(name) {
                        Some(v) => v.clone(),
                        None => {
                            let keys: Vec<&str> = m.keys().map(String::as_str).collect();
                            bail!("no field {name:?} (fields: {})", keys.join(", "))
                        }
                    },
                    (Value::List(l), "length") => Value::Num(l.len() as f64),
                    (Value::Str(s), "length") => Value::Num(s.chars().count() as f64),
                    _ => bail!("cannot read field {name:?} of a {}", base.type_name()),
                }
            }
            Expr::Index(base, idx) => {
                let base = self.eval(base)?;
                let idx = self.eval(idx)?;
                match (&base, &idx) {
                    (Value::List(l), Value::Num(i)) => {
                        let i = *i;
                        let j = if i < 0.0 { l.len() as f64 + i } else { i };
                        if j.fract() != 0.0 || j < 0.0 || j as usize >= l.len() {
                            bail!("index {i} out of range for a list of {}", l.len());
                        }
                        l[j as usize].clone()
                    }
                    (Value::Map(m), Value::Str(k)) => {
                        m.get(k).cloned().ok_or_else(|| Error(format!("no field {k:?}")))?
                    }
                    _ => bail!("cannot index a {} with a {}", base.type_name(), idx.type_name()),
                }
            }
            Expr::Not(x) => Value::Bool(!boolean(&self.eval(x)?, "!")?),
            Expr::Neg(x) => Value::Num(-num(&self.eval(x)?, "-")?),
            Expr::Binary(op, l, r) => self.binary(*op, l, r)?,
            Expr::Call(name, args) => self.call(name, args)?,
        })
    }

    fn binary(&mut self, op: BinOp, l: &Expr, r: &Expr) -> Result<Value, Error> {
        if matches!(op, BinOp::And | BinOp::Or) {
            let what = if op == BinOp::And { "&&" } else { "||" };
            let lv = boolean(&self.eval(l)?, what)?;
            if (op == BinOp::And && !lv) || (op == BinOp::Or && lv) {
                return Ok(Value::Bool(lv));
            }
            return Ok(Value::Bool(boolean(&self.eval(r)?, what)?));
        }
        let lv = self.eval(l)?;
        let rv = self.eval(r)?;
        let sym = match op {
            BinOp::Eq => "==",
            BinOp::Ne => "!=",
            BinOp::Lt => "<",
            BinOp::Le => "<=",
            BinOp::Gt => ">",
            BinOp::Ge => ">=",
            BinOp::Add => "+",
            BinOp::Sub => "-",
            BinOp::Mul => "*",
            BinOp::Div => "/",
            BinOp::Rem => "%",
            BinOp::And | BinOp::Or => unreachable!(),
        };
        Ok(match op {
            BinOp::Eq | BinOp::Ne => {
                let comparable = std::mem::discriminant(&lv) == std::mem::discriminant(&rv)
                    || matches!(lv, Value::Null)
                    || matches!(rv, Value::Null);
                if !comparable {
                    bail!("cannot compare a {} with a {} using {sym}", lv.type_name(), rv.type_name());
                }
                Value::Bool((lv == rv) == (op == BinOp::Eq))
            }
            BinOp::Lt | BinOp::Le | BinOp::Gt | BinOp::Ge => {
                let ord = match (&lv, &rv) {
                    (Value::Num(a), Value::Num(b)) => a.partial_cmp(b),
                    (Value::Str(a), Value::Str(b)) => Some(a.cmp(b)),
                    _ => bail!("cannot compare a {} with a {} using {sym}", lv.type_name(), rv.type_name()),
                };
                let Some(ord) = ord else { bail!("cannot compare NaN") };
                Value::Bool(match op {
                    BinOp::Lt => ord.is_lt(),
                    BinOp::Le => ord.is_le(),
                    BinOp::Gt => ord.is_gt(),
                    _ => ord.is_ge(),
                })
            }
            BinOp::Add if matches!((&lv, &rv), (Value::Str(_), Value::Str(_))) => {
                let (Value::Str(a), Value::Str(b)) = (lv, rv) else { unreachable!() };
                Value::Str(a + b.as_str())
            }
            _ => {
                let a = num(&lv, sym)?;
                let b = num(&rv, sym)?;
                Value::Num(match op {
                    BinOp::Add => a + b,
                    BinOp::Sub => a - b,
                    BinOp::Mul => a * b,
                    BinOp::Div | BinOp::Rem if b == 0.0 => bail!("division by zero"),
                    BinOp::Div => a / b,
                    _ => a % b,
                })
            }
        })
    }

    fn arg_value(&mut self, arg: &Arg, f: &str) -> Result<Value, Error> {
        match arg {
            Arg::Expr(e) => self.eval(e),
            Arg::Lambda(..) => bail!("{f}: unexpected function argument"),
        }
    }

    fn list_arg(&mut self, args: &[Arg], f: &str) -> Result<Vec<Value>, Error> {
        match args.first() {
            Some(a) => match self.arg_value(a, f)? {
                Value::List(l) => Ok(l),
                v => bail!("{f} needs a list, got {}", v.type_name()),
            },
            None => bail!("{f} needs a list"),
        }
    }

    /// Applies the optional lambda in `args[1]` to `item` (identity if absent).
    fn apply(&mut self, args: &[Arg], item: Value, f: &str) -> Result<Value, Error> {
        match args.get(1) {
            None => Ok(item),
            Some(Arg::Lambda(x, body)) => {
                self.locals.push((x.clone(), item));
                let r = self.eval(body);
                self.locals.pop();
                r
            }
            Some(Arg::Expr(_)) => bail!("{f}: second argument must be a function like x => x.amount"),
        }
    }

    fn call(&mut self, name: &str, args: &[Arg]) -> Result<Value, Error> {
        let f = name;
        let arity = |n: usize| -> Result<(), Error> {
            if args.len() == n { Ok(()) } else { bail!("{f} takes {n} argument(s), got {}", args.len()) }
        };
        Ok(match name {
            "len" | "length" => {
                arity(1)?;
                match self.arg_value(&args[0], f)? {
                    Value::List(l) => Value::Num(l.len() as f64),
                    Value::Str(s) => Value::Num(s.chars().count() as f64),
                    v => bail!("{f} needs a list or string, got {}", v.type_name()),
                }
            }
            "sum" | "avg" | "average" | "mean" => {
                let items = self.list_arg(args, f)?;
                if args.len() > 2 {
                    bail!("{f} takes a list and an optional function");
                }
                let n = items.len();
                let mut total = 0.0;
                for item in items {
                    total += num(&self.apply(args, item, f)?, f)?;
                }
                if f == "sum" {
                    Value::Num(total)
                } else if n == 0 {
                    bail!("{f} of an empty list");
                } else {
                    Value::Num(total / n as f64)
                }
            }
            "min" | "max" => {
                // min(list), min(list, x => ...), or min(a, b, ...)
                let first_is_list = match args.first() {
                    Some(Arg::Expr(e)) => matches!(self.eval(e)?, Value::List(_)),
                    _ => false,
                };
                let mut nums = Vec::new();
                if first_is_list && args.len() <= 2 && !matches!(args.get(1), Some(Arg::Expr(_))) {
                    for item in self.list_arg(args, f)? {
                        nums.push(num(&self.apply(args, item, f)?, f)?);
                    }
                } else {
                    for a in args {
                        nums.push(num(&self.arg_value(a, f)?, f)?);
                    }
                }
                let Some(&first) = nums.first() else { bail!("{f} of nothing") };
                Value::Num(nums.iter().fold(first, |m, &x| if f == "min" { m.min(x) } else { m.max(x) }))
            }
            "count" | "filter" | "all" | "every" | "any" | "some" => {
                let items = self.list_arg(args, f)?;
                if args.len() == 1 && f == "count" {
                    return Ok(Value::Num(items.len() as f64));
                }
                if !matches!(args.get(1), Some(Arg::Lambda(..))) || args.len() != 2 {
                    bail!("{f} needs a list and a condition like x => x.amount > 100");
                }
                let total = items.len();
                let mut kept = Vec::new();
                for item in items {
                    if boolean(&self.apply(args, item.clone(), f)?, f)? {
                        kept.push(item);
                    }
                }
                match f {
                    "count" => Value::Num(kept.len() as f64),
                    "filter" => Value::List(kept),
                    "all" | "every" => Value::Bool(kept.len() == total),
                    _ => Value::Bool(!kept.is_empty()),
                }
            }
            "map" => {
                arity(2)?;
                let items = self.list_arg(args, f)?;
                let mut out = Vec::new();
                for item in items {
                    out.push(self.apply(args, item, f)?);
                }
                Value::List(out)
            }
            "abs" | "round" | "floor" | "ceil" => {
                arity(1)?;
                let x = num(&self.arg_value(&args[0], f)?, f)?;
                Value::Num(match f {
                    "abs" => x.abs(),
                    "round" => x.round(),
                    "floor" => x.floor(),
                    _ => x.ceil(),
                })
            }
            "contains" | "includes" => {
                arity(2)?;
                let hay = self.arg_value(&args[0], f)?;
                let needle = self.arg_value(&args[1], f)?;
                match (&hay, &needle) {
                    (Value::List(l), _) => Value::Bool(l.contains(&needle)),
                    (Value::Str(s), Value::Str(n)) => Value::Bool(s.contains(n.as_str())),
                    _ => bail!("{f} needs a list or string"),
                }
            }
            "lower" | "toLowerCase" => {
                arity(1)?;
                match self.arg_value(&args[0], f)? {
                    Value::Str(s) => Value::Str(s.to_lowercase()),
                    v => bail!("{f} needs a string, got {}", v.type_name()),
                }
            }
            "days_between" => {
                arity(2)?;
                let a = self.arg_value(&args[0], f)?;
                let b = self.arg_value(&args[1], f)?;
                let (Value::Str(a), Value::Str(b)) = (&a, &b) else {
                    bail!("days_between needs two dates like \"2025-04-11\"");
                };
                Value::Num((parse_date(b)? - parse_date(a)?) as f64)
            }
            _ => bail!("unknown function {name:?}"),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn docs() -> Vec<(String, Value)> {
        let offer: serde_json::Value = serde_json::json!({
            "offer_amount": 1_350_000, "closing_days": 30, "contingencies": 0, "buyer": "Alice",
            "signed_at": "2025-04-11",
        });
        let book: serde_json::Value = serde_json::json!({
            "offers": [{"amount": 900_000}, {"amount": 1_200_000}, {"amount": 1_350_000}],
        });
        vec![
            ("offer".into(), Value::from_json(&offer)),
            ("book".into(), Value::from_json(&book)),
            ("now".into(), Value::Str("2025-04-20".into())),
        ]
    }

    fn t(src: &str) -> Result<bool, Error> {
        evaluate(&parse(src)?, &docs())
    }

    #[test]
    fn comparisons_and_logic() {
        assert!(t("offer.offer_amount > 1_300_000 && offer.closing_days <= 30").unwrap());
        assert!(!t("offer.offer_amount > 1.4e6").unwrap());
        assert!(t("offer.contingencies == 0 and not (offer.buyer == 'Bob')").unwrap());
        assert!(t("offer.offer_amount - 50000 >= 1300000").unwrap());
        assert!(t("offer.buyer === \"Alice\"").unwrap());
    }

    #[test]
    fn lists_and_lambdas() {
        assert!(t("book.offers.filter(o => o.amount > 1e6).length >= 2").unwrap());
        assert!(t("count(book.offers, o => o.amount >= 900000) == 3").unwrap());
        assert!(t("book.offers.every(o => o.amount > 800000)").unwrap());
        assert!(!t("book.offers.some(o => o.amount > 2e6)").unwrap());
        assert!(t("max(book.offers, o => o.amount) == offer.offer_amount").unwrap());
        assert!(t("sum(book.offers.map(o => o.amount)) == 3450000").unwrap());
        assert!(t("avg(book.offers, (o) => o.amount) == 1150000").unwrap());
        assert!(t("book.offers[-1].amount == 1350000").unwrap());
        assert!(t("min(3, 1, 2) == 1").unwrap());
    }

    #[test]
    fn dates() {
        assert!(t("days_between(offer.signed_at, now) <= 14").unwrap());
        assert!(t("offer.signed_at >= '2025-04-01'").unwrap());
    }

    #[test]
    fn errors_are_not_false() {
        assert!(t("offer.offer_amount").is_err(), "non-boolean result");
        assert!(t("offer.price > 1").is_err(), "missing field");
        assert!(t("offer.buyer > 1").is_err(), "type mismatch");
        assert!(t("1 < 2 < 3").is_err(), "chained comparison");
        assert!(t("offer.offer_amount > $1.3M").is_err());
        assert!(t("eval('1')").is_err());
        assert!(parse("").is_err());
    }
}
