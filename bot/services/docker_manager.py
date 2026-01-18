"""
Docker management service.
Provides container listing and Docker availability detection.
"""
from dataclasses import dataclass
from typing import List, Optional

from config import get_logger

logger = get_logger(__name__)

# Try to import docker, handle if not installed
try:
    import docker
    from docker.errors import DockerException
    DOCKER_SDK_AVAILABLE = True
except ImportError:
    DOCKER_SDK_AVAILABLE = False
    docker = None
    DockerException = Exception


@dataclass
class ContainerInfo:
    """Container information."""
    id: str
    short_id: str
    name: str
    image: str
    status: str
    health: Optional[str] = None
    ports: Optional[str] = None


@dataclass
class ContainerDetail:
    """Detailed container information."""
    id: str
    short_id: str
    name: str
    image: str
    status: str
    health: Optional[str] = None
    ports: Optional[str] = None
    created: Optional[str] = None
    started: Optional[str] = None
    ip_address: Optional[str] = None
    cpu_percent: Optional[float] = None
    memory_mb: Optional[float] = None
    memory_limit_mb: Optional[float] = None
    memory_percent: Optional[float] = None
    restart_count: int = 0
    command: Optional[str] = None
    environment: Optional[List[str]] = None


class DockerManager:
    """Manager for Docker operations."""

    def __init__(self) -> None:
        """Initialize DockerManager."""
        self._client = None
        self._available: Optional[bool] = None
        self._error_message: Optional[str] = None

    def is_available(self) -> bool:
        """
        Check if Docker is available on the system.
        
        Returns:
            True if Docker is available and accessible
        """
        if self._available is not None:
            return self._available
        
        if not DOCKER_SDK_AVAILABLE:
            self._available = False
            self._error_message = "Docker SDK not installed"
            logger.warning("Docker SDK (docker-py) is not installed")
            return False
        
        try:
            self._client = docker.from_env()
            # Test connection by pinging
            self._client.ping()
            self._available = True
            logger.info("Docker is available and connected")
            return True
        except DockerException as e:
            self._available = False
            self._error_message = str(e)
            logger.warning(f"Docker is not available: {e}")
            return False
        except Exception as e:
            self._available = False
            self._error_message = str(e)
            logger.warning(f"Error connecting to Docker: {e}")
            return False

    def get_error_message(self) -> Optional[str]:
        """Get the error message if Docker is not available."""
        return self._error_message

    def get_client(self):
        """Get Docker client (creates if needed)."""
        if self._client is None and self.is_available():
            pass  # is_available() already creates the client
        return self._client

    def list_containers(self, all_containers: bool = True) -> List[ContainerInfo]:
        """
        List Docker containers.
        
        Args:
            all_containers: Include stopped containers if True
            
        Returns:
            List of ContainerInfo objects
        """
        if not self.is_available():
            return []
        
        try:
            containers = self._client.containers.list(all=all_containers)
            result = []
            
            for container in containers:
                # Get health status if available
                health = None
                if hasattr(container, 'health') and container.health:
                    health = container.health
                elif 'Health' in container.attrs.get('State', {}):
                    health = container.attrs['State']['Health'].get('Status')
                
                # Get port mappings
                ports = self._format_ports(container.ports)
                
                # Get image name
                image_tags = container.image.tags
                image = image_tags[0] if image_tags else container.image.short_id
                
                result.append(ContainerInfo(
                    id=container.id,
                    short_id=container.short_id,
                    name=container.name,
                    image=image,
                    status=container.status,
                    health=health,
                    ports=ports,
                ))
            
            # Sort: running first, then by name
            status_order = {
                'running': 0,
                'restarting': 1,
                'paused': 2,
                'created': 3,
                'exited': 4,
                'dead': 5,
            }
            result.sort(key=lambda c: (status_order.get(c.status, 6), c.name.lower()))
            
            return result
            
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return []

    def _format_ports(self, ports: dict) -> Optional[str]:
        """Format port mappings to readable string."""
        if not ports:
            return None
        
        port_list = []
        for container_port, host_bindings in ports.items():
            if host_bindings:
                for binding in host_bindings:
                    host_port = binding.get('HostPort', '')
                    if host_port:
                        port_list.append(f"{host_port}→{container_port}")
            else:
                port_list.append(container_port)
        
        return ", ".join(port_list[:3]) if port_list else None  # Limit to 3 ports

    def get_container_count(self) -> dict:
        """
        Get container count summary.
        
        Returns:
            Dictionary with total, running, stopped counts
        """
        containers = self.list_containers(all_containers=True)
        
        running = sum(1 for c in containers if c.status == 'running')
        stopped = sum(1 for c in containers if c.status in ['exited', 'dead'])
        other = len(containers) - running - stopped
        
        return {
            'total': len(containers),
            'running': running,
            'stopped': stopped,
            'other': other,
        }

    def get_status_emoji(self, status: str) -> str:
        """Get emoji for container status."""
        status_emojis = {
            'running': '🟢',
            'restarting': '🔄',
            'paused': '⏸️',
            'created': '📦',
            'exited': '🔴',
            'dead': '💀',
        }
        return status_emojis.get(status, '❓')

    def get_container_by_id(self, container_id: str) -> Optional[ContainerDetail]:
        """
        Get detailed information about a specific container.
        
        Args:
            container_id: Container ID (short or full)
            
        Returns:
            ContainerDetail object or None if not found
        """
        if not self.is_available():
            return None
        
        try:
            container = self._client.containers.get(container_id)
            attrs = container.attrs
            config = attrs.get('Config', {})
            state = attrs.get('State', {})
            host_config = attrs.get('HostConfig', {})
            network_settings = attrs.get('NetworkSettings', {})
            
            # Get health status
            health = None
            if 'Health' in state:
                health = state['Health'].get('Status')
            
            # Get port mappings
            ports = self._format_ports(container.ports)
            
            # Get image name
            image_tags = container.image.tags
            image = image_tags[0] if image_tags else container.image.short_id
            
            # Get IP address
            ip_address = None
            networks = network_settings.get('Networks', {})
            for net_name, net_info in networks.items():
                if net_info.get('IPAddress'):
                    ip_address = net_info['IPAddress']
                    break
            
            # Get timestamps
            created = attrs.get('Created', '')[:19].replace('T', ' ') if attrs.get('Created') else None
            started = state.get('StartedAt', '')[:19].replace('T', ' ') if state.get('StartedAt') else None
            
            # Get restart count
            restart_count = attrs.get('RestartCount', 0)
            
            # Get command
            cmd = config.get('Cmd')
            command = ' '.join(cmd) if cmd else None
            
            # Get CPU and memory stats if running
            cpu_percent = None
            memory_mb = None
            memory_limit_mb = None
            memory_percent = None
            
            if container.status == 'running':
                try:
                    stats = container.stats(stream=False)
                    
                    # CPU calculation
                    cpu_stats = stats.get('cpu_stats', {})
                    precpu_stats = stats.get('precpu_stats', {})
                    
                    cpu_delta = cpu_stats.get('cpu_usage', {}).get('total_usage', 0) - \
                                precpu_stats.get('cpu_usage', {}).get('total_usage', 0)
                    system_delta = cpu_stats.get('system_cpu_usage', 0) - \
                                   precpu_stats.get('system_cpu_usage', 0)
                    online_cpus = cpu_stats.get('online_cpus', 1)
                    
                    if system_delta > 0 and cpu_delta > 0:
                        cpu_percent = round((cpu_delta / system_delta) * online_cpus * 100, 2)
                    
                    # Memory calculation
                    memory_stats = stats.get('memory_stats', {})
                    usage = memory_stats.get('usage', 0)
                    limit = memory_stats.get('limit', 0)
                    
                    if usage > 0:
                        memory_mb = round(usage / (1024 * 1024), 2)
                    if limit > 0:
                        memory_limit_mb = round(limit / (1024 * 1024), 2)
                        if usage > 0:
                            memory_percent = round((usage / limit) * 100, 2)
                except Exception as e:
                    logger.debug(f"Could not get stats for container {container.name}: {e}")
            
            return ContainerDetail(
                id=container.id,
                short_id=container.short_id,
                name=container.name,
                image=image,
                status=container.status,
                health=health,
                ports=ports,
                created=created,
                started=started,
                ip_address=ip_address,
                cpu_percent=cpu_percent,
                memory_mb=memory_mb,
                memory_limit_mb=memory_limit_mb,
                memory_percent=memory_percent,
                restart_count=restart_count,
                command=command,
            )
            
        except Exception as e:
            logger.error(f"Error getting container {container_id}: {e}")
            return None

    def get_containers_page(self, page: int = 0, per_page: int = 10) -> tuple:
        """
        Get a page of containers.
        
        Args:
            page: Page number (0-indexed)
            per_page: Number of containers per page
            
        Returns:
            Tuple of (containers_list, total_count, total_pages)
        """
        all_containers = self.list_containers(all_containers=True)
        total = len(all_containers)
        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        
        start = page * per_page
        end = start + per_page
        
        return all_containers[start:end], total, total_pages

    def start_container(self, container_id: str) -> tuple[bool, str]:
        """
        Start a container.
        
        Args:
            container_id: Container ID (short or full)
            
        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"
        
        try:
            container = self._client.containers.get(container_id)
            
            if container.status == 'running':
                return False, f"Container '{container.name}' is already running"
            
            container.start()
            logger.info(f"Container {container.name} started successfully")
            return True, f"Container '{container.name}' started successfully"
            
        except Exception as e:
            logger.error(f"Error starting container {container_id}: {e}")
            return False, f"Error starting container: {str(e)[:100]}"

    def stop_container(self, container_id: str, timeout: int = 10) -> tuple[bool, str]:
        """
        Stop a container.
        
        Args:
            container_id: Container ID (short or full)
            timeout: Timeout in seconds before killing
            
        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"
        
        try:
            container = self._client.containers.get(container_id)
            
            if container.status != 'running':
                return False, f"Container '{container.name}' is not running"
            
            container.stop(timeout=timeout)
            logger.info(f"Container {container.name} stopped successfully")
            return True, f"Container '{container.name}' stopped successfully"
            
        except Exception as e:
            logger.error(f"Error stopping container {container_id}: {e}")
            return False, f"Error stopping container: {str(e)[:100]}"

    def restart_container(self, container_id: str, timeout: int = 10) -> tuple[bool, str]:
        """
        Restart a container.
        
        Args:
            container_id: Container ID (short or full)
            timeout: Timeout in seconds before killing
            
        Returns:
            Tuple of (success, message)
        """
        if not self.is_available():
            return False, "Docker is not available"
        
        try:
            container = self._client.containers.get(container_id)
            container.restart(timeout=timeout)
            logger.info(f"Container {container.name} restarted successfully")
            return True, f"Container '{container.name}' restarted successfully"
            
        except Exception as e:
            logger.error(f"Error restarting container {container_id}: {e}")
            return False, f"Error restarting container: {str(e)[:100]}"
