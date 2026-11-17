{
  description = "Web UI to manage router port forwards over UPnP — no router login";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }: let
    inherit (flake-utils.lib) system;
    systems = [
      system.x86_64-linux
      system.aarch64-linux
      system.x86_64-darwin
      system.aarch64-darwin
    ];

  in flake-utils.lib.eachSystem systems (system: let
    pkgs = import nixpkgs { inherit system; };
    inherit (pkgs) lib stdenv;

    portforward-ui = stdenv.mkDerivation {
      pname = "portforward-ui";
      version = "0.1.0";
      src = ./.;

      nativeBuildInputs = [ pkgs.makeWrapper ];
      dontBuild = true;

      installPhase = ''
        runHook preInstall
        mkdir -p $out/bin $out/share/portforward-ui
        cp portforward_ui.py $out/share/portforward-ui/

        # Pure stdlib app; wrap with python3. On Linux, put `ip` on PATH so the
        # device-discovery dropdown can read the neighbour table (optional).
        makeWrapper ${pkgs.python3}/bin/python3 $out/bin/portforward-ui \
          --add-flags $out/share/portforward-ui/portforward_ui.py \
          ${lib.optionalString stdenv.isLinux
            "--prefix PATH : ${lib.makeBinPath [ pkgs.iproute2 ]}"}

        runHook postInstall
      '';

      meta = {
        description = "Web UI to manage router port forwards over UPnP";
        mainProgram = "portforward-ui";
        platforms = lib.platforms.unix;
      };
    };

  in {
    # `nix build`  /  `nix run`
    packages.default = portforward-ui;
    packages.portforward-ui = portforward-ui;
    apps.default = flake-utils.lib.mkApp {
      drv = portforward-ui;
      name = "portforward-ui";
    };

    # `nix develop` — then: python3 portforward_ui.py
    devShells.default = pkgs.mkShell {
      packages = [ pkgs.python3 ]
        ++ lib.optionals stdenv.isLinux [ pkgs.iproute2 ];
    };
  }) // {
    # NixOS service — the idiomatic way to run it on the mini PC.
    #   services.portforward-ui = { enable = true; openFirewall = true; };
    nixosModules.default = { config, lib, pkgs, ... }: let
      cfg = config.services.portforward-ui;
    in {
      options.services.portforward-ui = {
        enable = lib.mkEnableOption "the portforward-ui UPnP port-forward web UI";

        package = lib.mkOption {
          type = lib.types.package;
          default = self.packages.${pkgs.system}.default;
          defaultText = lib.literalExpression "portforward-ui.packages.\${system}.default";
          description = "The portforward-ui package to run.";
        };

        host = lib.mkOption {
          type = lib.types.str;
          default = "0.0.0.0";
          description = "Address to bind the web UI to.";
        };

        port = lib.mkOption {
          type = lib.types.port;
          default = 8765;
          description = "TCP port for the web UI.";
        };

        openFirewall = lib.mkOption {
          type = lib.types.bool;
          default = false;
          description = ''
            Open the UI port in the firewall. LAN use only — this UI has no
            authentication, so never expose it to the internet.
          '';
        };
      };

      config = lib.mkIf cfg.enable {
        systemd.services.portforward-ui = {
          description = "Port Forwarding UI (UPnP)";
          wantedBy = [ "multi-user.target" ];
          after = [ "network-online.target" ];
          wants = [ "network-online.target" ];

          environment = {
            HOST = cfg.host;
            PORT = toString cfg.port;
          };

          serviceConfig = {
            ExecStart = lib.getExe cfg.package;
            Restart = "on-failure";
            RestartSec = 3;

            # hardening — the app writes nothing to disk and only needs sockets.
            DynamicUser = true;
            NoNewPrivileges = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            PrivateTmp = true;
            # AF_NETLINK/AF_UNIX let `ip neigh` populate the device dropdown.
            RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_NETLINK" "AF_UNIX" ];
          };
        };

        networking.firewall.allowedTCPPorts =
          lib.mkIf cfg.openFirewall [ cfg.port ];
      };
    };
  };
}
