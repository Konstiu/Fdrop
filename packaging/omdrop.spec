# The portable half of Omdrop: the omdrop command and the AirDrop receiver,
# from brentkearney/omdrop-plugin, without the Omarchy panel (the QML files).
# The radio comes from a backend package; on Fedora that is omdrop-owl.
#
# Built by tools/build-rpm.sh, which supplies the source tarball (git archive
# of the upstream commit) and the Fedora patch. Nothing is downloaded here.
%global plugindir %{_datadir}/omdrop
%bcond check 1

Name:           omdrop
Version:        %{omdrop_version}
Release:        2%{?omdrop_snap:.%{omdrop_snap}}%{?dist}
Summary:        Send and receive files with nearby Apple devices over AirDrop
License:        MIT
URL:            https://github.com/brentkearney/omdrop-plugin
Source0:        omdrop-plugin-%{version}.tar.gz
# vercmp, library paths, firewalld, dnf and notify-send, for upstream.
Patch0:         omdrop-fedora.patch
BuildArch:      noarch

BuildRequires:  bash
%if %{with check}
BuildRequires:  python3
BuildRequires:  python3-libarchive-c
BuildRequires:  openssl
%endif

Requires:       bash
Requires:       python3
Requires:       python3-libarchive-c
Requires:       python3-gobject
Requires:       keyutils-libs
Requires:       openssl
Requires:       systemd
Requires:       iproute
Requires:       polkit
Requires:       hostname
Requires:       libnotify
Requires:       xdg-utils
# The radio backend (docs/radio-backend.md): makes awdl0 exist and this
# machine visible on it, and ships the sender, in /usr/lib/omdrop.
Requires:       omdrop-owl
Recommends:     wl-clipboard

%description
Omdrop makes this computer visible to nearby Apple devices for a bounded
window, receives what they send, and sends them files and links. This package
holds the omdrop command and the receiver it runs as a user service. The Wi-Fi
side is a separate radio backend package.

%prep
%autosetup -p1 -n omdrop-plugin-%{version}

%build

%install
install -d %{buildroot}%{plugindir}/bin %{buildroot}%{_bindir}
# manifest.json is how the command finds the directory it was installed in.
install -pm644 manifest.json README.md %{buildroot}%{plugindir}/
install -pm755 bin/omdrop %{buildroot}%{plugindir}/bin/
install -pm644 bin/*.py %{buildroot}%{plugindir}/bin/
cp -a bin/apple-ca share docs skills %{buildroot}%{plugindir}/
mv %{buildroot}%{plugindir}/apple-ca %{buildroot}%{plugindir}/bin/
rm -f %{buildroot}%{plugindir}/share/sounds/make-sounds.sh
ln -s ../share/omdrop/bin/omdrop %{buildroot}%{_bindir}/omdrop

%check
bash -n bin/omdrop
%if %{with check}
python3 -m unittest discover -s tests
%endif

# Nothing in a user's home is touched on removal: ~/.omdrop (the AirDrop
# identity), ~/.config/airdrop and the per-user service stay where they are.

%files
%license LICENSE
%doc README.md CONTRIBUTING.md
%{_bindir}/omdrop
%{plugindir}/

%changelog
* Tue Oct 13 2026 Konsti <konsti@localhost> - %{version}-1
- Package the omdrop command and receiver for Fedora
