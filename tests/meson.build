fs = import('fs')
venv_python = meson.project_source_root() / '.venv' / 'bin' / 'python'
if fs.exists(venv_python)
  python = find_program(venv_python)
else
  python = import('python').find_installation('python3')
endif

test_env = environment()
test_env.set('PYTHONPATH', meson.project_source_root())

test(
  'unit',
  python,
  args: ['-m', 'unittest', 'discover', '-s', meson.current_source_dir()],
  env: test_env,
)
