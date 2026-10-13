# Fdrop: the GTK4/libadwaita front end to the omdrop command.
# Built by tools/build-rpm.sh from a tarball of this repository.
%global appid io.github.konstiu.Fdrop

Name:           fdrop
Version:        %{gnome_version}
Release:        3%{?dist}
Summary:        AirDrop with nearby Apple devices, for GNOME
License:        MIT
URL:            https://github.com/brentkearney/omdrop-plugin
Source0:        fdrop-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3
BuildRequires:  desktop-file-utils
BuildRequires:  appstream

Requires:       python3
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4 >= 4.14
Requires:       libadwaita >= 1.6
Requires:       hicolor-icon-theme
# The protocol side: the omdrop command, which this window only drives.
Requires:       omdrop
# Its name before 0.2.0.
Obsoletes:      omdrop-gnome < 0.2.0
Provides:       omdrop-gnome = %{version}-%{release}
# The radar's sounds.
Recommends:     pipewire-utils

%description
A native GNOME window for AirDrop through the omdrop command: turn receiving on for a bounded time, see
nearby Apple devices on a radar, send them files and links, and open what they
send. It runs the omdrop command for everything and keeps no state of its own.

%prep
%autosetup -n fdrop-%{version}

%build

%install
install -d %{buildroot}%{_datadir}/fdrop/fdrop
install -pm644 src/fdrop/*.py %{buildroot}%{_datadir}/fdrop/fdrop/
# The launcher finds the code in ../share/fdrop.
install -Dpm755 fdrop %{buildroot}%{_bindir}/fdrop
install -Dpm644 data/%{appid}.desktop %{buildroot}%{_datadir}/applications/%{appid}.desktop
install -Dpm644 data/%{appid}.metainfo.xml %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
install -Dpm644 data/icons/hicolor/scalable/apps/%{appid}.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
install -Dpm644 data/icons/hicolor/symbolic/apps/%{appid}-symbolic.svg \
  %{buildroot}%{_datadir}/icons/hicolor/symbolic/apps/%{appid}-symbolic.svg

%check
# The launcher is a file, and its code is where it looks for it.
test -f %{buildroot}%{_bindir}/fdrop && test -d %{buildroot}%{_datadir}/fdrop/fdrop
desktop-file-validate %{buildroot}%{_datadir}/applications/%{appid}.desktop
appstreamcli validate --no-net %{buildroot}%{_metainfodir}/%{appid}.metainfo.xml
# The model's tests need no display; the window's skip themselves without one.
python3 -W ignore -m unittest tests.test_model

%files
%license LICENSE LICENSE.omdrop
%doc README.md
%{_bindir}/fdrop
%{_datadir}/fdrop/
%{_datadir}/applications/%{appid}.desktop
%{_metainfodir}/%{appid}.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/%{appid}.svg
%{_datadir}/icons/hicolor/symbolic/apps/%{appid}-symbolic.svg

%changelog
* Tue Oct 13 2026 Konsti <konsti@localhost> - %{version}-1
- Renamed from omdrop-gnome to fdrop
