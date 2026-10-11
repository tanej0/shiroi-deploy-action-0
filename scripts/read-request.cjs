const fs = require('node:fs');
const request = process.env.EVENT_NAME === 'push'
  ? JSON.parse(fs.readFileSync('update-request.json', 'utf8'))
  : {
      operation: process.env.OPERATION,
      source_ref: process.env.SOURCE_REF,
      force: process.env.FORCE === 'true',
    };
if (!['none', 'update', 'rollback', 'check'].includes(request.operation)) throw new Error('Unknown operation');
if (typeof request.source_ref !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$/.test(request.source_ref)) throw new Error('Invalid source ref');
if (typeof request.force !== 'boolean') throw new Error('force must be boolean');
fs.appendFileSync(process.env.GITHUB_OUTPUT, `operation=${request.operation}\nsource_ref=${request.source_ref}\nforce=${request.force}\n`);
