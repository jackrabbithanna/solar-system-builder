python = import('python').find_installation('python3')

test_env = environment()
test_env.set('PYTHONPATH', meson.project_source_root())

test(
  'unit',
  python,
  args: ['-m', 'unittest', 'discover', '-s', meson.current_source_dir()],
  env: test_env,
)
