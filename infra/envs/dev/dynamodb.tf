# Both tables are built from the JSON files the services own and test against (keys, indexes,
# TTL), so a table changes in one place: control-plane/dynamodb/ and worker/dynamodb/.

locals {
  tables = {
    control-plane = {
      definition = jsondecode(file("${local.repo}/control-plane/dynamodb/control-plane-table.json"))
      ttl        = jsondecode(file("${local.repo}/control-plane/dynamodb/control-plane-ttl.json"))
      # Specs and versions live here: the only data that cannot be rebuilt.
      protect = true
    }
    run-state = {
      definition = jsondecode(file("${local.repo}/worker/dynamodb/run-state-table.json"))
      ttl        = jsondecode(file("${local.repo}/worker/dynamodb/run-state-ttl.json"))
      # Working state of runs, deleted by TTL after 15 days anyway (ADR 0011).
      protect = false
    }
  }
}

resource "aws_dynamodb_table" "this" {
  for_each = local.tables

  name         = "${local.name}-${each.key}"
  billing_mode = each.value.definition.BillingMode
  hash_key     = one([for k in each.value.definition.KeySchema : k.AttributeName if k.KeyType == "HASH"])
  range_key    = one([for k in each.value.definition.KeySchema : k.AttributeName if k.KeyType == "RANGE"])

  dynamic "attribute" {
    for_each = each.value.definition.AttributeDefinitions
    content {
      name = attribute.value.AttributeName
      type = attribute.value.AttributeType
    }
  }

  dynamic "global_secondary_index" {
    for_each = try(each.value.definition.GlobalSecondaryIndexes, [])
    content {
      name            = global_secondary_index.value.IndexName
      projection_type = global_secondary_index.value.Projection.ProjectionType

      dynamic "key_schema" {
        for_each = global_secondary_index.value.KeySchema
        content {
          attribute_name = key_schema.value.AttributeName
          key_type       = key_schema.value.KeyType
        }
      }
    }
  }

  ttl {
    enabled        = each.value.ttl.TimeToLiveSpecification.Enabled
    attribute_name = each.value.ttl.TimeToLiveSpecification.AttributeName
  }

  point_in_time_recovery {
    enabled = each.value.protect
  }

  deletion_protection_enabled = each.value.protect
}
