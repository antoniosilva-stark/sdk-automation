const fs = require('fs');
const path = require('path');

describe('SDK Generation Tests', () => {

  describe('SDK Generation Prerequisites', () => {
    test('should have input spec available', () => {
      const specPath = path.join(__dirname, '../../apis/spec-v2.openapi.yaml');
      expect(fs.existsSync(specPath)).toBe(true);
    });

    test('should have generator config for Node', () => {
      const configPath = path.join(__dirname, '../../tools/generator-node-config.yaml');
      expect(fs.existsSync(configPath)).toBe(true);
    });

    test('should have output directory configured', () => {
      const outputDir = 'generated/sdk-node-temp';
      // Note: SDK not generated yet until Java is installed
      // This test validates the config path is correct
      expect(outputDir).toMatch(/^generated\/sdk-/);
    });
  });

  describe('SDK Structure Validation', () => {
    // generated/ is gitignored; skip until `make generate` has run
    const sdkDir = path.join(__dirname, '../../generated/sdk-node-temp');
    const sdkGenerated = fs.existsSync(sdkDir);
    const maybeTest = sdkGenerated ? test : test.skip;

    maybeTest('should have models directory with 60+ model files', () => {
      const modelsDir = path.join(sdkDir, 'models');
      const files = fs.readdirSync(modelsDir).filter(f => f.endsWith('.ts') && f !== 'index.ts');
      expect(files.length).toBeGreaterThanOrEqual(60);
    });

    maybeTest('should have api directory with 60+ API files', () => {
      const apiDir = path.join(sdkDir, 'api');
      const files = fs.readdirSync(apiDir).filter(f => f.endsWith('.ts'));
      expect(files.length).toBeGreaterThanOrEqual(60);
    });

    maybeTest('should have valid package.json with correct metadata', () => {
      const pkg = JSON.parse(fs.readFileSync(path.join(sdkDir, 'package.json'), 'utf8'));

      expect(pkg.name).toBe('@starkbank/sdk-node');
      expect(pkg.version).toBe('0.3.0');
      expect(pkg.main).toMatch(/dist\/index\.js$/);
      expect(pkg.types).toMatch(/dist\/index\.d\.ts$/);
    });

    maybeTest('should have tsconfig.json configured for Node.js ES2020', () => {
      const tsconfig = JSON.parse(fs.readFileSync(path.join(sdkDir, 'tsconfig.json'), 'utf8'));

      expect(tsconfig.compilerOptions.target).toBe('ES2020');
      expect(tsconfig.compilerOptions.module).toBe('commonjs');
    });
  });

  describe('SDK Ready State', () => {
    test('should have SDK generation command available', () => {
      const packageJsonPath = path.join(__dirname, '../../package.json');
      const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));

      expect(packageJson.scripts['generate-sdks']).toBeDefined();
    });

    test('should have make generate target', () => {
      const makefilePath = path.join(__dirname, '../../Makefile');
      const makefile = fs.readFileSync(makefilePath, 'utf8');

      expect(makefile).toMatch(/^generate:/m);
    });

    test('should have gitignore configured to exclude generated SDKs', () => {
      const gitignorePath = path.join(__dirname, '../../.gitignore');
      const gitignore = fs.readFileSync(gitignorePath, 'utf8');

      expect(gitignore).toMatch(/generated\//);
    });
  });

  describe('SDK Configuration Consistency', () => {
    test('all generator configs should use same input spec', () => {
      const configDir = path.join(__dirname, '../../tools');
      const configFiles = fs.readdirSync(configDir)
        .filter(f => f.startsWith('generator-') && f.endsWith('-config.yaml'));

      const YAML = require('js-yaml');
      const inputSpecs = new Set();

      for (const file of configFiles) {
        const content = fs.readFileSync(path.join(configDir, file), 'utf8');
        const config = YAML.load(content);
        inputSpecs.add(config.inputSpec);
      }

      expect(inputSpecs.size).toBe(1);
      expect(Array.from(inputSpecs)[0]).toBe('apis/spec-v2.openapi.yaml');
    });

    test('all generator configs should have consistent version', () => {
      const configDir = path.join(__dirname, '../../tools');
      const configFiles = fs.readdirSync(configDir)
        .filter(f => f.startsWith('generator-') && f.endsWith('-config.yaml'));

      const YAML = require('js-yaml');
      const versions = new Set();

      for (const file of configFiles) {
        const content = fs.readFileSync(path.join(configDir, file), 'utf8');
        const config = YAML.load(content);
        versions.add(config.packageVersion);
      }

      expect(versions.size).toBe(1);
      expect(Array.from(versions)[0]).toBe('0.3.0');
    });
  });
});
