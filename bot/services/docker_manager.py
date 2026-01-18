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
