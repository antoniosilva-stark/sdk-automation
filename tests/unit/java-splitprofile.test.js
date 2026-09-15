const fs = require('fs');
const path = require('path');
const YAML = require('js-yaml');

describe('Java SplitProfile Pilot (Entrega 4)', () => {

  describe('Template & Config', () => {
    test('should have the custom Java model template', () => {
      const templatePath = path.join(__dirname, '../../templates/java/model.mustache');
      expect(fs.existsSync(templatePath)).toBe(true);
    });

    test('should have generator config for Java', () => {
      const configPath = path.join(__dirname, '../../tools/generator-java-config.yaml');
      expect(fs.existsSync(configPath)).toBe(true);
    });

    test('generator config for Java should not reference the pilot templateDir', () => {
      const configPath = path.join(__dirname, '../../tools/generator-java-config.yaml');
      const config = YAML.load(fs.readFileSync(configPath, 'utf8'));

      expect(config.templateDir).toBeUndefined();
    });

    test('spec should define SplitProfile with real fields, not the generic placeholder', () => {
      const specPath = path.join(__dirname, '../../apis/spec-v2.openapi.yaml');
      const spec = YAML.load(fs.readFileSync(specPath, 'utf8'));
      const schema = spec.components.schemas.SplitProfile;

      expect(Object.keys(schema.properties)).toEqual(
        expect.arrayContaining(['interval', 'delay', 'tags', 'status', 'created', 'updated'])
      );
      expect(spec.paths['/split_profile'].put).toBeDefined();
      expect(spec.paths['/split_profile'].post).toBeUndefined();
    });
  });

  describe('Generated SplitProfile.java', () => {
    const javaFile = path.join(
      __dirname,
      '../../generated/sdk-java-temp/src/main/java/com/starkbank/SplitProfile.java'
    );
    const generated = fs.existsSync(javaFile);
    const maybeTest = generated ? test : test.skip;
    const content = generated ? fs.readFileSync(javaFile, 'utf8') : '';

    maybeTest('should declare the real com.starkbank package', () => {
      expect(content).toMatch(/^package com\.starkbank;/m);
    });

    maybeTest('should extend Resource and declare ClassData', () => {
      expect(content).toMatch(/class SplitProfile extends Resource/);
      expect(content).toMatch(/ClassData data = new ClassData\(SplitProfile\.class, "SplitProfile"\)/);
    });

    maybeTest('should have the real fields from the spec', () => {
      for (const field of ['interval', 'delay', 'tags', 'status', 'created', 'updated']) {
        expect(content).toMatch(new RegExp(`public \\S+ ${field};`));
      }
    });

    maybeTest('should have put/get/query with a single canonical signature each', () => {
      expect(content.match(/static List<SplitProfile> put\(/g)).toHaveLength(1);
      expect(content.match(/static SplitProfile get\(/g)).toHaveLength(1);
      expect(content.match(/static Generator<SplitProfile> query\(/g)).toHaveLength(1);
      expect(content).toMatch(/put\(List<\?> profiles, User user\)/);
    });

    maybeTest('should not include convenience overloads or the Log sub-resource (reduced scope)', () => {
      expect(content).not.toMatch(/class Log extends Resource/);
      expect(content.match(/static \w+(<[^>]+>)? (put|get|query)\(/g)).toHaveLength(3);
    });
  });

  describe('sdk-sync.yaml workflow', () => {
    const workflowPath = path.join(__dirname, '../../.github/workflows/sdk-sync.yaml');
    const workflow = YAML.load(fs.readFileSync(workflowPath, 'utf8'));

    test('should exist with the 3 expected jobs', () => {
      expect(Object.keys(workflow.jobs)).toEqual(['validate', 'drift', 'sync']);
    });

    test('should trigger manually, not on push', () => {
      expect(workflow.on).toHaveProperty('workflow_dispatch');
      expect(workflow.on.push).toBeUndefined();
    });

    test('should authenticate via GitHub App only, with no PAT or registry secrets', () => {
      const raw = fs.readFileSync(workflowPath, 'utf8');

      expect(raw).toMatch(/SDK_APP_ID/);
      expect(raw).toMatch(/SDK_APP_PRIVATE_KEY/);
      expect(raw).not.toMatch(/SDK_REPOS_TOKEN/);
      for (const registrySecret of ['NPM_TOKEN', 'PYPI_API_TOKEN', 'MAVEN_CENTRAL_TOKEN', 'GEM_HOST_API_KEY', 'NUGET_API_KEY']) {
        expect(raw).not.toMatch(registrySecret);
      }
    });

    test('should not have a publish-to-registry stage', () => {
      const jobNames = Object.keys(workflow.jobs);
      expect(jobNames).not.toContain('publish');
    });
  });
});
