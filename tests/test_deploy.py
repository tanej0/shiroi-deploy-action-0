"""Exercise real filesystem changes with isolated PM2/HTTP stubs, never live apps."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='yohaku-test-')
        self.root = Path(self.tmp.name)
        self.base = self.root / 'blog'
        self.old = self.base / '17/standalone/apps/web/server.js'
        self.old.parent.mkdir(parents=True)
        self.old.write_text('// old frontend\n')
        (self.base / 'server.js').symlink_to(self.old)
        (self.base / '.env').write_text('EXAMPLE=local-only\n')
        (self.base / 'ecosystem.config.js').write_text('// existing custom PM2 settings\n')
        (self.base / '.fake-running').write_text(str(self.old))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.stub('node', '#!/bin/bash\npwd > "$YOH_DEPLOY_BASE/.fake-preview"\nexec /bin/sleep 3600\n')
        self.stub('sleep', '#!/bin/bash\nexec /bin/sleep 0.02\n')
        self.stub('ss', '#!/bin/bash\nif test -f "$YOH_DEPLOY_BASE/BUSY_PORT"; then echo "LISTEN 0 128 127.0.0.1:12323 *:*"; fi\n')
        self.stub('pm2', '''#!/bin/bash
set -e
case "$1" in
  describe) exit 0 ;;
  reload)
    target=$(readlink -f "$YOH_DEPLOY_BASE/server.js")
    test ! -f "$(dirname "$target")/FAIL_RELOAD" || exit 1
    echo "$target" > "$YOH_DEPLOY_BASE/.fake-running" ;;
  save) touch "$YOH_DEPLOY_BASE/.fake-saved" ;;
  *) exit 3 ;;
esac
''')
        self.stub('curl', '''#!/bin/bash
set -e
url=${!#}
if [[ "$url" == *:12323/* ]]; then
  test -f "$YOH_DEPLOY_BASE/.fake-preview" || exit 22
  dir=$(cat "$YOH_DEPLOY_BASE/.fake-preview")
  test ! -f "$dir/FAIL_PREVIEW" || exit 22
else
  target=$(cat "$YOH_DEPLOY_BASE/.fake-running")
  test ! -f "$(dirname "$target")/FAIL_LIVE" || exit 22
fi
printf 200
''')
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        YOH_DEPLOY_BASE=str(self.base), YOH_HEALTH_ATTEMPTS='8')
        self.stage = self.root / 'upload'
        self.stage.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def bundle(self, failure=None):
        package = self.root / 'package'
        entry = package / 'standalone/apps/web/server.js'
        entry.parent.mkdir(parents=True)
        entry.write_text('// new frontend\n')
        (package / 'source.commit').write_text(SHA + '\n')
        if failure:
            (entry.parent / failure).touch()
        archive = self.stage / 'release.tar.gz'
        with tarfile.open(str(archive), 'w:gz') as handle:
            handle.add(str(package / 'standalone'), arcname='standalone')
            handle.add(str(package / 'source.commit'), arcname='source.commit')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.stage / 'release.tar.gz.sha256').write_text(digest + '  release.tar.gz\n')
        shutil.copyfile(str(ROOT / 'scripts/deploy.sh'), str(self.stage / 'deploy.sh'))

    def run_script(self, *args):
        return subprocess.run(['bash', str(ROOT / 'scripts/deploy.sh')] + list(args),
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=15)

    def deploy(self):
        return self.run_script('deploy', str(self.stage), '100-1', SHA)

    def assert_old(self):
        self.assertEqual((self.base / 'server.js').resolve(), self.old)
        self.assertEqual((self.base / '.fake-running').read_text().strip(), str(self.old))

    def test_success_and_manual_rollback(self):
        self.bundle()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = self.base / '100-1/standalone/apps/web/server.js'
        self.assertEqual((self.base / 'server.js').resolve(), new)
        self.assertEqual((new.parent / '.env').resolve(), self.base / '.env')
        self.assertEqual((self.base / '.deploy-state/current-commit').read_text().strip(), SHA)
        self.assertEqual((self.base / 'ecosystem.config.js').read_text(), '// existing custom PM2 settings\n')
        self.assertFalse(self.stage.exists())
        self.assertEqual(self.run_script('rollback').returncode, 0)
        self.assert_old()
        self.assertEqual((self.base / '.deploy-state/current-commit').read_text().strip(), 'unknown')

    def test_candidate_failure_does_not_switch(self):
        self.bundle('FAIL_PREVIEW')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Candidate failed', result.stderr)
        self.assert_old()

    def test_candidate_uses_matching_loopback_hostname(self):
        self.bundle()
        self.stub('node', '''#!/bin/bash
test "$HOSTNAME" = localhost || exit 1
pwd > "$YOH_DEPLOY_BASE/.fake-preview"
exec /bin/sleep 3600
''')
        curl = self.bin / 'curl'
        curl.write_text(curl.read_text().replace(
            'test -f "$YOH_DEPLOY_BASE/.fake-preview" || exit 22',
            '[[ "$url" == http://localhost:12323/ ]] || exit 22\n'
            '  test -f "$YOH_DEPLOY_BASE/.fake-preview" || exit 22'))
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_live_failure_restores_previous(self):
        self.bundle('FAIL_LIVE')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Previous frontend restored', result.stderr)
        self.assert_old()

    def test_pm2_reload_failure_restores_previous(self):
        self.bundle('FAIL_RELOAD')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Previous frontend restored', result.stderr)
        self.assert_old()

    def test_checksum_failure_never_switches(self):
        self.bundle()
        with (self.stage / 'release.tar.gz').open('ab') as handle:
            handle.write(b'corrupt')
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assert_old()

    def test_busy_preview_port_never_switches(self):
        self.bundle()
        (self.base / 'BUSY_PORT').touch()
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assert_old()

    def test_unhealthy_baseline_aborts_update(self):
        self.bundle()
        (self.old.parent / 'FAIL_LIVE').touch()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Current frontend is unhealthy', result.stderr)
        self.assert_old()

    def test_manual_rollback_can_recover_unhealthy_live_site(self):
        self.bundle()
        self.assertEqual(self.deploy().returncode, 0)
        (self.base / '100-1/standalone/apps/web/FAIL_LIVE').touch()
        self.assertEqual(self.run_script('rollback').returncode, 0)
        self.assert_old()

    def test_archive_contains_static_assets_but_no_environment_files(self):
        source = self.root / 'source'
        web = source / 'apps/web'
        entry = web / '.next/standalone/apps/web'
        entry.mkdir(parents=True)
        (entry / 'server.js').write_text('// sample\n')
        (entry / '.env.production').write_text('EXAMPLE=must-not-be-packaged\n')
        (web / '.next/static').mkdir()
        (web / '.next/static/site.js').write_text('// static\n')
        (web / 'public').mkdir()
        (web / 'public/logo.svg').write_text('<svg/>\n')
        output = self.root / 'packaged'
        env = dict(self.env, SOURCE_SHA=SHA)
        result = subprocess.run(['bash', str(ROOT / 'scripts/package.sh'), str(source), str(output)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(str(output / 'release.tar.gz')) as handle:
            names = handle.getnames()
        self.assertIn('standalone/apps/web/.next/static/site.js', names)
        self.assertIn('standalone/apps/web/public/logo.svg', names)
        self.assertFalse(any(Path(name).name.startswith('.env') for name in names))


if __name__ == '__main__':
    unittest.main()
