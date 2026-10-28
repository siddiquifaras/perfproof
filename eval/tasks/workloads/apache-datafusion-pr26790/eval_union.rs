//! Time `UnionExec::try_new` over many inputs whose schemas differ only
//! in one child's field metadata, with non-nullable fields.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use arrow::datatypes::{DataType, Field, Schema};
use datafusion_physical_plan::empty::EmptyExec;
use datafusion_physical_plan::union::UnionExec;
use datafusion_physical_plan::ExecutionPlan;

const FIELDS: usize = 10;
const CHILDREN: usize = 1000;
const ROUNDS: usize = 5;

fn schema(distinct: bool) -> Arc<Schema> {
    let fields: Vec<Field> = (0..FIELDS)
        .map(|i| {
            let mut meta = HashMap::new();
            meta.insert("k0".to_string(), format!("v{i}"));
            meta.insert("k1".to_string(), format!("w{i}"));
            if distinct {
                meta.insert("last".to_string(), "x".to_string());
            }
            Field::new(format!("c{i}"), DataType::Int64, false)
                .with_metadata(meta)
        })
        .collect();
    Arc::new(Schema::new(fields))
}

fn main() {
    let mut best = f64::MAX;
    for _ in 0..ROUNDS {
        let inputs: Vec<Arc<dyn ExecutionPlan>> = (0..CHILDREN)
            .map(|i| {
                Arc::new(EmptyExec::new(schema(i == CHILDREN - 1)))
                    as Arc<dyn ExecutionPlan>
            })
            .collect();
        let start = Instant::now();
        let plan = UnionExec::try_new(inputs).expect("union");
        best = best.min(start.elapsed().as_secs_f64());
        std::hint::black_box(plan);
    }
    println!("best of {ROUNDS}: {best:.6} s for {CHILDREN} children");
}
