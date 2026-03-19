from unittest import TestCase
from unittest.mock import patch

from battlestats.env import load_default_env_files, load_env_file, resolve_db_host, resolve_db_user


class EnvBootstrapTests(TestCase):
    @patch('battlestats.env.dotenv')
    def test_load_env_file_prefers_read_dotenv_when_available(self, mock_dotenv):
        mock_dotenv.read_dotenv = object()
        mock_dotenv.load_dotenv = object()

        with patch.object(mock_dotenv, 'read_dotenv') as mock_read, patch.object(mock_dotenv, 'load_dotenv') as mock_load:
            load_env_file('.env')

        mock_read.assert_called_once_with('.env')
        mock_load.assert_not_called()

    @patch('battlestats.env.dotenv')
    def test_load_env_file_falls_back_to_load_dotenv(self, mock_dotenv):
        if hasattr(mock_dotenv, 'read_dotenv'):
            del mock_dotenv.read_dotenv
        mock_dotenv.load_dotenv = object()

        with patch.object(mock_dotenv, 'load_dotenv') as mock_load:
            load_env_file('.env')

        mock_load.assert_called_once_with('.env')

    @patch('battlestats.env.load_env_file')
    def test_load_default_env_files_loads_existing_base_and_secret_files_in_order(self, mock_load_env_file):
        loaded_paths = load_default_env_files('/tmp')

        self.assertEqual(loaded_paths, [])
        mock_load_env_file.assert_not_called()

    @patch('battlestats.env.load_env_file')
    @patch('pathlib.Path.exists')
    def test_load_default_env_files_returns_existing_files(self, mock_exists, mock_load_env_file):
        mock_exists.side_effect = [True, True]

        loaded_paths = load_default_env_files('/tmp')

        self.assertEqual([path.name for path in loaded_paths], [
                         '.env', '.env.secrets'])
        self.assertEqual(
            [call.args[0] for call in mock_load_env_file.call_args_list],
            ['/tmp/.env', '/tmp/.env.secrets'],
        )

    @patch.dict('os.environ', {'DB_HOST': 'db'}, clear=False)
    @patch('battlestats.env.running_in_container', return_value=False)
    def test_resolve_db_host_maps_docker_service_name_to_localhost_for_host_runs(self, _mock_running):
        self.assertEqual(resolve_db_host(), '127.0.0.1')

    @patch.dict('os.environ', {'DB_HOST': 'db'}, clear=False)
    @patch('battlestats.env.running_in_container', return_value=True)
    def test_resolve_db_host_keeps_docker_service_name_in_container(self, _mock_running):
        self.assertEqual(resolve_db_host(), 'db')

    @patch.dict('os.environ', {'DB_USER': 'compose-user'}, clear=True)
    def test_resolve_db_user_accepts_db_user(self):
        self.assertEqual(resolve_db_user(), 'compose-user')

    @patch.dict('os.environ', {'DB_USERNAME': 'settings-user', 'DB_USER': 'compose-user'}, clear=True)
    def test_resolve_db_user_prefers_db_username_when_present(self):
        self.assertEqual(resolve_db_user(), 'settings-user')
