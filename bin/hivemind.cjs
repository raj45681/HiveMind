#!/usr/bin/env node
'use strict';

try {
  process.exitCode = require('../npm/launcher.cjs').run(process.argv.slice(2));
} catch (error) {
  console.error('HiveMind setup: ' + error.message);
  process.exitCode = 1;
}
