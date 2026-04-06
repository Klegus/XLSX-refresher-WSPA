const dbName = "Lesson_test";
const testDb = db.getSiblingDB(dbName);

testDb.system_config.updateOne(
  { _id: "config" },
  {
    $set: {
      check_interval: 3600,
      maintenance_mode: false,
      maintenance_reason: null,
      last_check_stats: {
        total_plans: 0,
        plans_checked: 0,
        changes_detected: 0,
        timestamp: new Date().toISOString()
      }
    }
  },
  { upsert: true }
);

testDb.plans_config.updateOne(
  { _id: "plans_json" },
  {
    $set: {
      plans: {},
      source: "docker_test_init",
      last_updated: new Date().toISOString()
    }
  },
  { upsert: true }
);

print(`[init] Seeded Mongo test database: ${dbName}`);
