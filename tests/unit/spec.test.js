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
    test('should have 60 resources with 120 paths', () => {
      const paths = Object.keys(spec.paths);
      expect(paths.length).toBe(120);
    });

    test('should have 180 operationId entries (60 resources × 3 operations)', () => {
      let operationIds = 0;
      for (const path in spec.paths) {
        const pathItem = spec.paths[path];
        if (pathItem.post?.operationId) operationIds++;
        if (pathItem.put?.operationId) operationIds++;
        if (pathItem.get?.operationId) operationIds++;
      }
      expect(operationIds).toBe(180);
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
      const externalResources = ['Transaction', 'Invoice', 'Transfer'];

      for (const resource of externalResources) {
        const schema = spec.components.schemas[resource];
        expect(schema).toBeDefined();
        expect(schema.$ref).toBeDefined();
        expect(schema.$ref).toMatch(/\.\/schemas\/.*\.yaml/);
      }
    });

    test('should have inline schemas for other resources', () => {
      const resource = 'Balance';
      const schema = spec.components.schemas[resource];

      expect(schema).toBeDefined();
      expect(schema.type).toBe('object');
      expect(schema.properties).toBeDefined();
      expect(schema.required).toContain('id');
    });

    test('should have 120 total schemas (60 resources × 2: create + model)', () => {
      const schemas = Object.keys(spec.components.schemas);
      expect(schemas.length).toBe(120);
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

    test('should have no breaking changes detected', () => {
      try {
        const output = execSync('python3 tools/breaking-change-detector.py', {
          cwd: path.join(__dirname, '../../'),
          encoding: 'utf8'
        });
        expect(output).toMatch(/\[OK\] nenhuma breaking change detectada/);
      } catch (error) {
        throw new Error(`Breaking change detector failed: ${error.message}`);
      }
    }, 15000);
  });
});
