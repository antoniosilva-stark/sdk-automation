#!/usr/bin/env node

/**
 * Breaking Change Detector
 *
 * Detects breaking changes between current and previous OpenAPI spec
 * Used in GitHub Actions CI/CD to block merges with BC
 *
 * Exit codes:
 *   0 = No breaking changes found
 *   1 = Breaking changes detected (merge blocked)
 */

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');
const { execSync } = require('child_process');

const SPEC_FILE = 'apis/spec-v2.openapi.yaml';

/**
 * Load spec from file or git
 * @param {string} ref - 'HEAD' for previous, null for current
 * @returns {object} Parsed OpenAPI spec
 */
function loadSpec(ref = null) {
  try {
    let specYaml;

    if (ref) {
      // Load from git
      specYaml = execSync(`git show ${ref}:${SPEC_FILE}`, { encoding: 'utf8' });
    } else {
      // Load from filesystem
      specYaml = fs.readFileSync(SPEC_FILE, 'utf8');
    }

    return yaml.load(specYaml);
  } catch (error) {
    if (ref === 'HEAD') {
      // First commit, no previous version
      console.log('ℹ️  No previous spec found (first commit)');
      return null;
    }
    throw error;
  }
}

/**
 * Detect breaking changes between two specs
 * @param {object} prevSpec - Previous OpenAPI spec
 * @param {object} currSpec - Current OpenAPI spec
 * @returns {array} List of breaking changes
 */
function detectBreakingChanges(prevSpec, currSpec) {
  const changes = [];

  if (!prevSpec) {
    return changes; // First commit
  }

  const prevPaths = prevSpec.paths || {};
  const currPaths = currSpec.paths || {};
  const prevSchemas = prevSpec.components?.schemas || {};
  const currSchemas = currSpec.components?.schemas || {};

  // Check for removed paths (operations)
  for (const [path, pathItem] of Object.entries(prevPaths)) {
    if (!currPaths[path]) {
      changes.push({
        type: 'removed_path',
        severity: 'MAJOR',
        path: path,
        message: `Operation removed: ${path}`
      });
      continue;
    }

    // Check for removed methods
    for (const method of ['get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace']) {
      if (pathItem[method] && !currPaths[path][method]) {
        changes.push({
          type: 'removed_operation',
          severity: 'MAJOR',
          path: path,
          method: method.toUpperCase(),
          message: `Operation removed: ${method.toUpperCase()} ${path}`
        });
      }
    }
  }

  // Check for removed schemas
  for (const schemaName of Object.keys(prevSchemas)) {
    if (!currSchemas[schemaName]) {
      changes.push({
        type: 'removed_schema',
        severity: 'MAJOR',
        schema: schemaName,
        message: `Schema removed: ${schemaName}`
      });
    }
  }

  // Check for removed required properties
  for (const [schemaName, schema] of Object.entries(prevSchemas)) {
    if (!currSchemas[schemaName]) continue;

    const prevRequired = schema.required || [];
    const currRequired = currSchemas[schemaName].required || [];
    const prevProperties = schema.properties || {};
    const currProperties = currSchemas[schemaName].properties || {};

    // Check for removed required fields
    for (const field of prevRequired) {
      if (!currRequired.includes(field)) {
        changes.push({
          type: 'removed_required_field',
          severity: 'MAJOR',
          schema: schemaName,
          field: field,
          message: `Required field removed: ${schemaName}.${field}`
        });
      }
    }

    // Check for new required fields (breaking for clients)
    for (const field of currRequired) {
      if (prevProperties[field] && !prevRequired.includes(field)) {
        changes.push({
          type: 'field_became_required',
          severity: 'MAJOR',
          schema: schemaName,
          field: field,
          message: `Field became required: ${schemaName}.${field}`
        });
      }
    }

    // Check for type changes
    for (const [field, prevProp] of Object.entries(prevProperties)) {
      if (!currProperties[field]) continue;

      const prevType = prevProp.type;
      const currType = currProperties[field].type;

      if (prevType && currType && prevType !== currType) {
        changes.push({
          type: 'type_changed',
          severity: 'MAJOR',
          schema: schemaName,
          field: field,
          prevType: prevType,
          currType: currType,
          message: `Type changed: ${schemaName}.${field} ${prevType} → ${currType}`
        });
      }
    }
  }

  return changes;
}

/**
 * Format and print changes
 * @param {array} changes - List of changes
 */
function printChanges(changes) {
  if (changes.length === 0) {
    console.log('✅ No breaking changes detected');
    return;
  }

  console.error('\n⚠️  BREAKING CHANGES DETECTED:\n');

  const byType = {};
  changes.forEach(change => {
    if (!byType[change.severity]) {
      byType[change.severity] = [];
    }
    byType[change.severity].push(change);
  });

  for (const [severity, items] of Object.entries(byType)) {
    console.error(`${severity}:`);
    items.forEach(item => {
      console.error(`  - ${item.message}`);
    });
    console.error('');
  }
}

/**
 * Main execution
 */
async function main() {
  try {
    console.log('🔍 Detecting breaking changes...\n');

    // Load specs
    const prevSpec = loadSpec('HEAD');
    const currSpec = loadSpec();

    // Detect changes
    const changes = detectBreakingChanges(prevSpec, currSpec);

    // Print results
    printChanges(changes);

    // Exit with appropriate code
    if (changes.length > 0) {
      console.error('\n⚠️  Merge blocked: Breaking changes require Tech-Lead approval\n');
      process.exit(1);
    }

    process.exit(0);
  } catch (error) {
    console.error('❌ Error detecting breaking changes:');
    console.error(error.message);
    process.exit(1);
  }
}

main();