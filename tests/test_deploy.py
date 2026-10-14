"""Exercise real filesystem changes with isolated PM2/HTTP stubs, never live apps."""
import hashlib
import json
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
        self.live_process = subprocess.Popen(['/bin/sleep', '3600'], cwd=str(self.old.parent))
        (self.base / '.fake-pid').write_text(str(self.live_process.pid))
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.stub('node', '#!/bin/bash\npwd > "$YOH_DEPLOY_BASE/.fake-preview"\nexec /bin/sleep 3600\n')
        self.stub('sleep', '#!/bin/bash\nexec /bin/sleep 0.02\n')
        self.stub('ss', '#!/bin/bash\nif test -f "$YOH_DEPLOY_BASE/BUSY_PORT"; then echo "LISTEN 0 128 127.0.0.1:12323 *:*"; fi\n')
        self.stub('pm2', '''#!/bin/bash
set -e
case "$1" in
  describe) test -f "$YOH_DEPLOY_BASE/.fake-running" ;;
  pid) cat "$YOH_DEPLOY_BASE/.fake-pid" ;;
  delete)
    kill "$(cat "$YOH_DEPLOY_BASE/.fake-pid")" 2>/dev/null || true
    rm -f "$YOH_DEPLOY_BASE/.fake-running" "$YOH_DEPLOY_BASE/.fake-pid" ;;
  start)
    target=$(readlink -f "$YOH_DEPLOY_BASE/server.js")
    test ! -f "$(dirname "$target")/FAIL_RELOAD" || exit 1
    (cd "$(dirname "$target")"; exec /bin/sleep 3600) </dev/null >/dev/null 2>&1 &
    pid=$!
    echo "$pid" > "$YOH_DEPLOY_BASE/.fake-pid"
    for ((i=0; i<100; i++)); do
      [[ $(readlink -f "/proc/$pid/cwd") == "$(dirname "$target")" ]] && break
      /bin/sleep 0.01
    done
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
        pid_file = self.base / '.fake-pid'
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), 15)
            except ProcessLookupError:
                pass
        if self.live_process.poll() is None:
            self.live_process.terminate()
        self.live_process.wait(timeout=5)
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
        rollback = self.run_script('rollback')
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assert_old()
        self.assertEqual((self.base / '.deploy-state/current-commit').read_text().strip(), 'unknown')

    def test_candidate_failure_does_not_switch(self):
        self.bundle('FAIL_PREVIEW')
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Candidate failed', result.stderr)
        self.assert_old()

    def test_stale_symlink_preserves_actual_running_release_for_rollback(self):
        self.bundle()
        stale = self.base / 'stale/standalone/apps/web/server.js'
        stale.parent.mkdir(parents=True)
        stale.write_text('// linked but never running\n')
        (self.base / 'server.js').unlink()
        (self.base / 'server.js').symlink_to(stale)
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.base / '.deploy-state/previous-entry').read_text().strip(), str(self.old))
        pid = int((self.base / '.fake-pid').read_text())
        self.assertEqual(Path('/proc/{}/cwd'.format(pid)).resolve(),
                         self.base / '100-1/standalone/apps/web')
        rollback = self.run_script('rollback')
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
        self.assert_old()

    def test_runtime_config_overrides_hardcoded_script_and_preserves_options(self):
        self.bundle()
        original = {'apps': [{'name': 'Yohaku', 'script': str(self.old),
                             'cwd': str(self.base), 'max_memory_restart': '180M',
                             'env': {'EXAMPLE': 'preserved'}},
                            {'name': 'other', 'script': '/example/other.js'}]}
        (self.base / 'ecosystem.config.js').write_text(
            'module.exports = ' + json.dumps(original) + ';\n')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        real_node = shutil.which('node', path=os.environ['PATH'])
        parsed = subprocess.run(
            [real_node, '-e', 'process.stdout.write(JSON.stringify(require(process.argv[1])))',
             str(self.base / '.deploy-state/runtime.config.cjs')],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        config = json.loads(parsed.stdout)
        self.assertEqual(config['apps'][0]['script'],
                         str(self.base / '100-1/standalone/apps/web/server.js'))
        self.assertEqual(config['apps'][0]['env'], original['apps'][0]['env'])
        self.assertEqual(config['apps'][0]['max_memory_restart'], '180M')
        self.assertEqual(config['apps'][1], original['apps'][1])
        self.assertEqual((self.base / 'ecosystem.config.js').read_text(),
                         'module.exports = ' + json.dumps(original) + ';\n')

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
        rollback = self.run_script('rollback')
        self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
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

    def test_recovery_restores_stopped_app_and_preserves_crontab(self):
        self.bundle()
        self.assertEqual(self.deploy().returncode, 0)
        subprocess.run(['pm2', 'delete', 'Yohaku'], env=self.env, check=True)
        cron_store = self.base / '.fake-crontab'
        original = '15 3 * * * /example/existing-task\n'
        cron_store.write_text(original)
        self.stub('crontab', '''#!/bin/bash
if [[ "$1" == -l ]]; then cat "$YOH_DEPLOY_BASE/.fake-crontab"; else cp "$1" "$YOH_DEPLOY_BASE/.fake-crontab"; fi
''')
        for _ in range(2):
            recovered = subprocess.run(
                ['bash', str(ROOT / 'scripts/recover-frontend.sh'), 'install-autostart'],
                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True, timeout=15)
            self.assertEqual(recovered.returncode, 0, recovered.stdout + recovered.stderr)
        cron_text = cron_store.read_text()
        self.assertIn(original, cron_text)
        self.assertEqual(cron_text.count('# codex-yohaku-autostart'), 1)
        self.assertTrue((self.base / 'boot-frontend.sh').exists())
        self.assertIn('Current frontend restored', recovered.stdout)
        self.assertEqual((self.base / '.fake-running').read_text().strip(),
                         str(self.base / '100-1/standalone/apps/web/server.js'))


if __name__ == '__main__':
    unittest.main()
