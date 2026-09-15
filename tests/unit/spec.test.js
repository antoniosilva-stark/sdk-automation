const fs = require('fs');
const path = require('path');
const YAML = require('js-yaml');
const { execSync } = require('child_process');

describe('OpenAPI Specification Tests', () => {
  let spec;

  beforeAll(() => {
    const specPath = path.join(__dirname, '../../apis/spec-v2.openapi.yaml');
    const specContent = fs.readFileSync(specPath, 'utf8');
    spec = YAML.load(specContent);
  });

  describe('Spec Structure', () => {
    test('every path should belong to a declared schema', () => {
      const paths = Object.keys(spec.paths);
      expect(paths.length).toBeGreaterThan(0);

      const schemas = new Set(Object.keys(spec.components.schemas));
      for (const route of paths) {
        const resource = route.replace('/{id}', '').replace(/^\//, '').split(/[-_]/)
.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('');
        const known = [...schemas].some((name) => name.toLowerCase() === resource.toLowerCase());
        expect(known).toBe(true);
      }
    });

    test('every operation should have a unique operationId', () => {
      const seen = new Set();

      for (const route in spec.paths) {
        for (const [verb, operation] of Object.entries(spec.paths[route])) {
          expect(operation.operationId).toBeDefined();
          expect(seen.has(operation.operationId)).toBe(false);
          seen.add(operation.operationId);
        }
      }

      expect(seen.size).toBeGreaterThan(0);
    });

    test('should have correct version number (v0.2.0)', () => {
      expect(spec.info.version).toBe('0.2.0');
    });

    test('should have valid servers configuration', () => {
      expect(spec.servers).toBeDefined();
      expect(spec.servers.length).toBeGreaterThan(0);
      expect(spec.servers[0].url).toMatch(/starkbank.com/);
    });
  });

  describe('Schema References', () => {
    test('should preserve external $refs for Transaction/Invoice/Transfer', () => {
      const schemasDir = path.join(__dirname, '../../apis/schemas');
      const applied = fs.readdirSync(schemasDir).filter((name) => name.endsWith('.yaml'));
      expect(applied.length).toBeGreaterThanOrEqual(3);

      for (const file of applied) {
        const content = YAML.load(fs.readFileSync(path.join(schemasDir, file), 'utf8'));
        const resource = Object.keys(content.components.schemas).find((name) => !name.endsWith('Create'));
        const schema = spec.components.schemas[resource];

        expect(schema).toBeDefined();
        expect(schema.$ref).toBe(`./schemas/${file}#/components/schemas/${resource}`);
      }
    });

    test('should keep not yet applied resources inline', () => {
      const stubs = Object.entries(spec.components.schemas)
.filter(([name, schema]) => !name.endsWith('Create') && !schema.$ref);

      expect(stubs.length).toBeGreaterThan(0);

      const [, schema] = stubs[0];
      expect(schema.type).toBe('object');
      expect(schema.properties).toBeDefined();
      expect(schema.required).toContain('id');
    });

    test('every resource schema should have a Create counterpart', () => {
      const schemas = Object.keys(spec.components.schemas);
      const resources = schemas.filter((name) => !name.endsWith('Create'));

      expect(resources.length).toBeGreaterThan(0);
      for (const resource of resources) {
        expect(schemas).toContain(`${resource}Create`);
      }
    });
  });

  describe('Spec Validation', () => {
    test('should pass openapi-spec-validator', () => {
      try {
        const output = execSync('npm run validate-spec', {
          encoding: 'utf8',
          stdio: ['pipe', 'pipe', 'pipe']
        });
        expect(output).toMatch(/OK|valid/i);
      } catch (error) {
        throw new Error(`Spec validation failed: ${error.message}`);
      }
    });

    test('breaking change detector gives a legible verdict against the default branch', () => {
      let output;
      let detected = false;
      try {
        output = execSync('python3 tools/breaking-change-detector.py --base origin/development', {
          cwd: path.join(__dirname, '../../'),
          encoding: 'utf8'
        });
      } catch (error) {
        if (error.status !== 1) {
          throw new Error(`Breaking change detector failed: ${error.message}`);
        }
        output = error.stdout;
        detected = true;
      }

      expect(output).toMatch(/verificando breaking changes/);
      if (detected) {
        expect(output).toMatch(/breaking change\(s\) detectada\(s\)/);
        expect(output).toMatch(/regra violada, governance\.md/);
      } else {
        expect(output).toMatch(/\[OK\] nenhuma breaking change detectada/);
      }
    }, 15000);
  });
});
