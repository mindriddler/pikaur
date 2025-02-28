"""Licensed under GPLv3, see https://www.gnu.org/licenses/"""

import sys
from datetime import datetime
from fnmatch import fnmatch
from typing import TYPE_CHECKING, ClassVar

import pyalpm

from .args import parse_args
from .aur import AURPackageInfo
from .config import VERSION, AurSearchSortingValues, PikaurConfig, UpgradeSortingValues
from .core import DEFAULT_TIMEZONE, InstallInfo
from .i18n import translate, translate_many
from .pacman import OFFICIAL_REPOS, PackageDB
from .pprint import (
    Colors,
    ColorsHighlight,
    bold_line,
    color_line,
    format_paragraph,
    get_term_width,
    print_stderr,
    print_stdout,
    print_warning,
)
from .version import get_common_version, get_version_diff

if TYPE_CHECKING:
    # pylint: disable=cyclic-import
    from collections.abc import Iterable, Sequence
    from typing import Final, TypeVar

    from .core import AURInstallInfo, PackageSource, RepoInstallInfo
    from .install_info_fetcher import InstallInfoFetcher

    AnyPackage = AURPackageInfo | pyalpm.Package
    InstallInfoT = TypeVar("InstallInfoT", bound=InstallInfo)


GROUP_COLOR: "Final" = Colors.blue
REPLACEMENTS_COLOR: "Final" = ColorsHighlight.cyan
ORPHANED_COLOR: "Final" = ColorsHighlight.red


def print_version(pacman_version: str, pyalpm_version: str, *, quiet: bool = False) -> None:
    if quiet:
        print_stdout(f"Pikaur v{VERSION}")
        print_stdout(f"{pacman_version} - pyalpm v{pyalpm_version}")
    else:
        year = str(datetime.now(tz=DEFAULT_TIMEZONE).year)
        sys.stdout.write(r"""
      /:}               _
     /--1             / :}
    /   |           / `-/
   |  ,  --------  /   /     Pikaur v""" + VERSION + r"""
   |'                 Y      (C) 2018-""" + year + r""" Pikaur development team
  /                   l      Licensed under GPLv3
  l  /       \        l
  j  ●   .   ●        l      """ + pacman_version + r"""
 { )  ._,.__,   , -.  {      pyalpm v""" + pyalpm_version + r"""
  Y    \  _/     ._/   \

""")


def print_not_found_packages(not_found_packages: list[str], *, repo: bool = False) -> None:
    num_packages = len(not_found_packages)
    print_warning(
        bold_line(
            translate_many(
                "Following package cannot be found in repositories:",
                "Following packages cannot be found in repositories:",
                num_packages,
            )
            if repo else
            translate_many(
                "Following package cannot be found in AUR:",
                "Following packages cannot be found in AUR:",
                num_packages,
            ),
        ),
    )
    for package in not_found_packages:
        print_stderr(format_paragraph(package))


class RepoColorGenerator:

    _type_storage: ClassVar[dict[str, int]] = {}
    _cache: ClassVar[dict[str, dict[str, int]]] = {}
    _init_done = False

    _MAX_COLOR = 15

    @classmethod
    def do_init(cls) -> None:
        if cls._init_done:
            return

        cls._init_done = True
        for official_repo_name in OFFICIAL_REPOS:
            cls.get_next("repo", official_repo_name)
        for repo in PackageDB.get_alpm_handle().get_syncdbs():
            cls.get_next("repo", repo.name)

    @classmethod
    def get_next(cls, color_type: str, _id: str) -> int:
        cls.do_init()
        if cls._cache.get(color_type, {}).get(_id):
            return cls._cache[color_type][_id]

        if (
                not cls._type_storage.get(color_type) or
                cls._type_storage[color_type] >= cls._MAX_COLOR
        ):
            cls._type_storage[color_type] = 10
        else:
            cls._type_storage[color_type] += 1

        cls._cache.setdefault(color_type, {})
        cls._cache[color_type][_id] = cls._type_storage[color_type]
        return cls._cache[color_type][_id]


def pretty_format_repo_name(repo_name: str, *, color: bool = True) -> str:
    result = f"{repo_name}/"
    if not color:
        return result
    return color_line(result, RepoColorGenerator.get_next("repo", repo_name))


def pretty_format_upgradeable(  # pylint: disable=too-many-statements
        packages_updates: "Sequence[InstallInfo]",
        *,
        verbose: bool = False,
        print_repo: bool = False,
        color: bool = True,
        template: str | None = None,
) -> str:
    parent_color = color

    def _color_line(line: str, color: int, reset: bool | None = None) -> str:
        if not parent_color:
            return line
        if reset is not None:
            return color_line(line, color_number=color, reset=reset)
        return color_line(line, color_number=color)

    def _bold_line(line: str) -> str:
        return bold_line(line) if parent_color else line

    # @TODO: python 3.11
    # type_sort_key = tuple[str, *tuple[int, ...]] | str
    type_sort_key = (
        tuple[str, str] | tuple[str, int] |
        tuple[int, str] | str
    )

    def pretty_format(  # pylint:disable=too-many-locals,R0912
            pkg_update: "InstallInfo",
    ) -> tuple[str, type_sort_key]:
        common_version, diff_weight = get_common_version(
            pkg_update.current_version or "", pkg_update.new_version or "",
        )
        user_config = PikaurConfig()
        color_config = user_config.colors
        version_color = color_config.Version.get_int()
        old_color = color_config.VersionDiffOld.get_int()
        new_color = color_config.VersionDiffNew.get_int()
        column_width = min(int(get_term_width() / 2.5), 37)

        sort_by: type_sort_key = (
            -diff_weight,
            pkg_update.name,
        )
        user_chosen_sorting = user_config.sync.UpgradeSorting
        if user_chosen_sorting == UpgradeSortingValues.PKGNAME:
            sort_by = pkg_update.name
        elif user_chosen_sorting == UpgradeSortingValues.REPO:
            sort_by = (
                pkg_update.repository or "zzz_(aur)",
                pkg_update.name,
            )

        pkg_name = pkg_update.name
        pkg_len = len(pkg_update.name)

        pkg_name = _bold_line(pkg_name)
        if (print_repo or verbose) and pkg_update.repository:
            pkg_name = f"{pretty_format_repo_name(pkg_update.repository, color=color)}{pkg_name}"
            pkg_len += len(pkg_update.repository) + 1
        elif print_repo:
            pkg_name = "{}{}".format(  # pylint: disable=consider-using-f-string
                _color_line("aur/", ColorsHighlight.red),
                pkg_name,
            )
            pkg_len += len("aur/")

        if pkg_update.required_by:
            required_by = " ({})".format(  # pylint: disable=consider-using-f-string
                translate("for {pkg}").format(
                    pkg=", ".join([p.package.name for p in pkg_update.required_by]),
                ),
            )
            pkg_len += len(required_by)
            dep_color = Colors.yellow
            required_by = _color_line(" ({})", dep_color).format(
                translate("for {pkg}").format(
                    pkg=_color_line(", ", dep_color).join([
                        _color_line(p.package.name, dep_color + 8) for p in pkg_update.required_by
                    ]) + _color_line("", dep_color, reset=False),
                ),
            )
            pkg_name += required_by
        if pkg_update.provided_by:
            provided_by = " ({})".format(  # pylint: disable=consider-using-f-string
                " # ".join([p.name for p in pkg_update.provided_by]),
            )
            pkg_len += len(provided_by)
            pkg_name += _color_line(provided_by, Colors.green)
        if pkg_update.members_of:
            members_of = " ({})".format(  # pylint: disable=consider-using-f-string
                translate_many("{grp} group", "{grp} groups", len(pkg_update.members_of)).format(
                    grp=", ".join(g for g in pkg_update.members_of),
                ),
            )
            pkg_len += len(members_of)
            members_of = _color_line(" ({})", GROUP_COLOR).format(
                translate_many("{grp} group", "{grp} groups", len(pkg_update.members_of)).format(
                    grp=_color_line(", ", GROUP_COLOR).join(
                        [_color_line(g, GROUP_COLOR + 8) for g in pkg_update.members_of],
                    ) + _color_line("", GROUP_COLOR, reset=False),
                ),
            )
            pkg_name += _color_line(members_of, GROUP_COLOR)
        if pkg_update.replaces:
            replaces = " (replaces {})".format(  # pylint: disable=consider-using-f-string
                ", ".join(g for g in pkg_update.replaces),
            )
            pkg_len += len(replaces)
            pkg_name += _color_line(replaces, REPLACEMENTS_COLOR)
            if not color:
                pkg_name = f"# {pkg_name}"

        pkg_size = ""
        if (
                user_config.sync.ShowDownloadSize.get_bool() and
                isinstance(pkg_update.package, pyalpm.Package)
        ):
            pkg_size = f"{pkg_update.package.size/1024/1024:.2f} MiB"

        days_old = ""
        if pkg_update.devel_pkg_age_days:
            days_old = " " + translate("({} days old)").format(pkg_update.devel_pkg_age_days)

        if (
                isinstance(pkg_update.package, AURPackageInfo) and
                pkg_update.maintainer is None
        ):
            orphaned = f" [{translate('orphaned')}]"
            pkg_len += len(orphaned)
            pkg_name += _color_line(orphaned, ORPHANED_COLOR)

        out_of_date = ""
        if (
                isinstance(pkg_update.package, AURPackageInfo) and
                pkg_update.package.outofdate is not None
        ):
            out_of_date = _color_line(
                " [{}: {}]".format(  # pylint: disable=consider-using-f-string
                    translate("outofdate"),
                    datetime.fromtimestamp(
                        pkg_update.package.outofdate,
                        tz=DEFAULT_TIMEZONE,
                    ).strftime("%Y/%m/%d"),
                ),
                color_config.VersionDiffOld.get_int(),
            )

        return (
            template or (
                " {pkg_name}{spacing}"
                " {current_version}{spacing2}"
                "{version_separator}{new_version}{spacing3}"
                "{pkg_size}{days_old}{out_of_date}{verbose}"
            )
        ).format(
            pkg_name=pkg_name,
            current_version=(
                _color_line(common_version, version_color) +
                _color_line(
                    get_version_diff(pkg_update.current_version or "", common_version),
                    old_color,
                )
            ),
            new_version=(
                _color_line(common_version, version_color) +
                _color_line(
                    get_version_diff(pkg_update.new_version or "", common_version),
                    new_color,
                )
            ),
            version_separator=(
                " -> " if (pkg_update.current_version or pkg_update.new_version) else ""
            ),
            spacing=" " * max(1, (column_width - pkg_len)),
            spacing2=" " * max(1, (
                column_width - 18 -
                len(pkg_update.current_version or "") -
                max(-1, (pkg_len - column_width))
            )),
            spacing3=(" " * max(1, (
                column_width - 18 -
                len(pkg_update.new_version or "") -
                max(-1, (pkg_len - column_width))
            )) if pkg_size else ""),
            pkg_size=pkg_size,
            days_old=days_old,
            out_of_date=out_of_date,
            verbose=(
                "" if not (verbose and pkg_update.description)
                else f"\n{format_paragraph(pkg_update.description)}"
            ),
        ), sort_by

    return "\n".join([
        line for line, _ in sorted(
            [
                pretty_format(pkg_update)
                for pkg_update in packages_updates
            ],
            key=lambda x: x[1],
        )
    ])


class SysupgradePrettyFormatter:

    def __init__(
        self,
        install_info: "InstallInfoFetcher",
        *,
        verbose: bool,
        manual_package_selection: bool,
    ) -> None:
        self.color = True
        self.install_info = install_info
        self.verbose = verbose

        self.repo_packages_updates: list[RepoInstallInfo] = \
            install_info.repo_packages_install_info[::]
        self.thirdparty_repo_packages_updates: list[RepoInstallInfo] = \
            install_info.thirdparty_repo_packages_install_info[::]
        self.aur_updates: list[AURInstallInfo] = \
            install_info.aur_updates_install_info[::]
        self.repo_replacements: list[RepoInstallInfo] = \
            install_info.repo_replacements_install_info[::]
        self.thirdparty_repo_replacements: list[RepoInstallInfo] = \
            install_info.thirdparty_repo_replacements_install_info[::]

        self.new_repo_deps: list[RepoInstallInfo] = \
            install_info.new_repo_deps_install_info[::]
        self.new_thirdparty_repo_deps: list[RepoInstallInfo] = \
            install_info.new_thirdparty_repo_deps_install_info[::]
        self.new_aur_deps: list[AURInstallInfo] = \
            install_info.aur_deps_install_info[::]

        if manual_package_selection:
            self.color = False
            self.new_repo_deps = []
            self.new_thirdparty_repo_deps = []
            self.new_aur_deps = []

        self.all_install_info_lists: Sequence[
            list[AURInstallInfo] | list[RepoInstallInfo]
        ] = [
            self.repo_packages_updates,
            self.thirdparty_repo_packages_updates,
            self.aur_updates,
            self.repo_replacements,
            self.thirdparty_repo_replacements,
            self.new_repo_deps,
            self.new_thirdparty_repo_deps,
            self.new_aur_deps,
        ]

        self.config = PikaurConfig()
        self.result: list[str] = []

    def _color_line(self, line: str, color: int) -> str:
        return color_line(line, color_number=color) if self.color else line

    def _bold_line(self, line: str) -> str:
        return bold_line(line) if self.color else line

    def pretty_format_upgradeable(
            self,
            install_infos: "Sequence[InstallInfo]",
            print_repo: bool | None = None,
    ) -> str:
        if print_repo is None:
            print_repo = self.config.sync.AlwaysShowPkgOrigin.get_bool()
        return pretty_format_upgradeable(
            install_infos,
            verbose=self.verbose, color=self.color, print_repo=print_repo,
        )

    def pformat_warned_packages(self) -> None:
        warn_about_packages_str = self.config.ui.WarnAboutPackageUpdates.get_str()
        warn_about_packages_list: list[InstallInfo] = []

        def remove_globs_from_pkg_list(pkg_list: "list[InstallInfoT]") -> None:
            pkg_install_info: InstallInfoT
            for pkg_install_info in pkg_list[::]:
                for glob in globs_and_names:
                    if fnmatch(pkg_install_info.name, glob):
                        pkg_list.remove(pkg_install_info)
                        warn_about_packages_list.append(pkg_install_info)

        if warn_about_packages_str:
            globs_and_names = warn_about_packages_str.split(",")
            pkg_list: list[RepoInstallInfo] | list[AURInstallInfo]
            for pkg_list in self.all_install_info_lists:
                remove_globs_from_pkg_list(pkg_list)  # type: ignore[misc]

        if warn_about_packages_list:
            self.result.append("\n{} {} {} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.blue),
                self._color_line("!!", ColorsHighlight.red),
                self._color_line(
                    translate_many(
                        "WARNING about package installation:",
                        "WARNING about packages installation:",
                        len(warn_about_packages_list),
                    ), ColorsHighlight.red,
                ),
                self._color_line("!!", ColorsHighlight.red),
            ))
            self.result.append(self.pretty_format_upgradeable(warn_about_packages_list))

    def pformat_replacements(self) -> None:
        if self.repo_replacements:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.blue),
                self._bold_line(translate_many(
                    "Repository package suggested as a replacement:",
                    "Repository packages suggested as a replacement:",
                    len(self.repo_replacements))),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.repo_replacements,
            ))
        if self.thirdparty_repo_replacements:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.blue),
                self._bold_line(translate_many(
                    "Third-party repository package suggested as a replacement:",
                    "Third-party repository packages suggested as a replacement:",
                    len(self.repo_packages_updates))),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.thirdparty_repo_replacements,
            ))

    def pformat_repo(self) -> None:
        if self.repo_packages_updates:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.blue),
                self._bold_line(translate_many(
                    "Repository package will be installed:",
                    "Repository packages will be installed:",
                    len(self.repo_packages_updates))),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.repo_packages_updates,
            ))
        if self.new_repo_deps:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.yellow),
                self._bold_line(translate_many(
                    "New dependency will be installed from repository:",
                    "New dependencies will be installed from repository:",
                    len(self.new_repo_deps),
                )),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.new_repo_deps,
            ))

    def pformat_thirdaprty_repo(self) -> None:
        if self.thirdparty_repo_packages_updates:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.blue),
                self._bold_line(translate_many(
                    "Third-party repository package will be installed:",
                    "Third-party repository packages will be installed:",
                    len(self.thirdparty_repo_packages_updates),
                )),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.thirdparty_repo_packages_updates,
                print_repo=True,
            ))
        if self.new_thirdparty_repo_deps:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.yellow),
                self._bold_line(translate_many(
                    "New dependency will be installed from third-party repository:",
                    "New dependencies will be installed from third-party repository:",
                    len(self.new_thirdparty_repo_deps),
                )),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.new_thirdparty_repo_deps,
            ))

    def pformat_aur(self) -> None:
        if self.aur_updates:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.cyan),
                self._bold_line(translate_many(
                    "AUR package will be installed:",
                    "AUR packages will be installed:",
                    len(self.aur_updates),
                )),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.aur_updates,
                print_repo=False,
            ))
        if self.new_aur_deps:
            self.result.append("\n{} {}".format(  # pylint: disable=consider-using-f-string
                self._color_line("::", ColorsHighlight.yellow),
                self._bold_line(translate_many(
                    "New dependency will be installed from AUR:",
                    "New dependencies will be installed from AUR:",
                    len(self.new_aur_deps),
                )),
            ))
            self.result.append(self.pretty_format_upgradeable(
                self.new_aur_deps,
                print_repo=False,
            ))

    def pformat_total_size(self) -> None:
        if self.config.sync.ShowDownloadSize.get_bool():
            self.result.append(
                "\n" +
                self._bold_line(translate("Total Download Size:")) +
                f"{str(round(self.install_info.get_total_download_size(), 2)).rjust(10)} MiB"
                "\n" +
                self._bold_line(translate("Total Installed Size:")) +
                f"{str(round(self.install_info.get_total_installed_size(), 2)).rjust(9)} MiB"
                "\n",
            )
        else:
            self.result += [""]

    def __call__(self) -> str:
        self.pformat_warned_packages()
        self.pformat_replacements()
        self.pformat_repo()
        self.pformat_thirdaprty_repo()
        self.pformat_aur()
        self.pformat_total_size()
        return "\n".join(self.result)


def pretty_format_sysupgrade(
        install_info: "InstallInfoFetcher",
        *,
        verbose: bool = False,
        manual_package_selection: bool = False,
) -> str:
    return SysupgradePrettyFormatter(
        install_info=install_info,
        verbose=verbose,
        manual_package_selection=manual_package_selection,
    )()


def print_ignored_package(
        package_name: str | None = None,
        install_info: InstallInfo | None = None,
        ignored_from: str | None = None,
) -> None:
    if not (package_name or install_info):
        missing_property_error = translate(
            "Either `{prop1}` or `{prop2}` should be set",
        ).format(
            prop1="package_name",
            prop2="install_info",
        )
        raise TypeError(missing_property_error)
    install_info = install_info or InstallInfo(
        name=package_name,
        current_version="",
        new_version="",
        package=None,
    )
    message = " ".join((
        color_line("::", ColorsHighlight.yellow),
        translate("Ignoring package update {}").format(
            pretty_format_upgradeable(
                [install_info],
                template="{pkg_name} ({current_version} => {new_version})",
            ))
        if (install_info.current_version and install_info.new_version) else
        translate("Ignoring package {}").format(
            pretty_format_upgradeable(
                [install_info],
                template=(
                    "{pkg_name} {current_version}"
                    if install_info.current_version else
                    "{pkg_name} {new_version}"
                ),
            )),
    ))
    if ignored_from:
        message += f" {ignored_from}"
    print_stderr(message)


def _get_local_version(package_name: str) -> str:
    return PackageDB.get_local_dict()[package_name].version


def print_package_uptodate(package_name: str, package_source: "PackageSource") -> None:
    print_warning(
        translate("{name} {version} {package_source} package is up to date - skipping").format(
            name=package_name,
            version=bold_line(_get_local_version(package_name)),
            package_source=package_source.name,
        ),
    )


def print_local_package_newer(package_name: str, aur_version: str) -> None:
    print_warning(
        translate(
            "{name} {version} local package is newer than in AUR ({aur_version}) - skipping",
        ).format(
            name=package_name,
            version=bold_line(_get_local_version(package_name)),
            aur_version=bold_line(aur_version),
        ),
    )


def print_package_downgrading(package_name: str, downgrade_version: str) -> None:
    print_warning(
        translate("Downgrading AUR package {name} {version} to {downgrade_version}").format(
            name=bold_line(package_name),
            version=bold_line(_get_local_version(package_name)),
            downgrade_version=bold_line(downgrade_version),
        ),
    )


def print_ignoring_outofdate_upgrade(package_info: InstallInfo) -> None:
    print_warning(
        translate("{name} {version} AUR package marked as 'outofdate' - skipping").format(
            name=package_info.name,
            version=bold_line(package_info.new_version),
        ),
    )


# pylint:disable=too-many-locals,too-many-statements,too-many-branches
def print_package_search_results(
        repo_packages: "Iterable[pyalpm.Package]",
        aur_packages: "Iterable[AURPackageInfo]",
        local_pkgs_versions: dict[str, str],
        *,
        enumerated: bool = False,
) -> "list[AnyPackage]":

    repos = [db.name for db in PackageDB.get_alpm_handle().get_syncdbs()]
    user_config = PikaurConfig()
    group_by_repo = user_config.ui.GroupByRepository.get_bool()

    def get_repo_sort_key(pkg: pyalpm.Package) -> tuple[int, str]:
        return (
            repos.index(pkg.db.name)
            if group_by_repo and pkg.db.name in repos
            else 999,
            pkg.name,
        )

    type_aur_sort_key = tuple[float, float] | float

    def get_aur_sort_key(pkg: AURPackageInfo) -> tuple[type_aur_sort_key, str]:
        user_aur_sort = user_config.ui.AurSearchSorting
        pkg_numvotes = pkg.numvotes if isinstance(pkg.numvotes, int) else 0
        pkg_popularity = pkg.popularity if isinstance(pkg.popularity, float) else 0.0

        if user_aur_sort == AurSearchSortingValues.PKGNAME:
            return (-1.0, pkg.name)
        if user_aur_sort == AurSearchSortingValues.POPULARITY:
            return ((-pkg_popularity, -pkg_numvotes), pkg.name)
        if user_aur_sort == AurSearchSortingValues.NUMVOTES:
            return ((-pkg_numvotes, -pkg_popularity), pkg.name)
        if user_aur_sort == AurSearchSortingValues.LASTMODIFIED:
            return (
                -pkg.lastmodified
                if isinstance(pkg.lastmodified, int)
                else 0,
                pkg.name)
        return (-(pkg_numvotes + 1) * (pkg_popularity + 1), pkg.name)

    args = parse_args()
    local_pkgs_names = local_pkgs_versions.keys()

    sorted_repo_pkgs: list[pyalpm.Package] = sorted(
        repo_packages,
        key=get_repo_sort_key,
    )
    sorted_aur_pkgs: list[AURPackageInfo] = sorted(
        aur_packages,
        key=get_aur_sort_key,
    )
    sorted_packages: list[AnyPackage] = [*sorted_repo_pkgs, *sorted_aur_pkgs]
    # mypy is always funny ^^ https://github.com/python/mypy/issues/5492#issuecomment-545992992

    enumerated_packages = list(enumerate(sorted_packages))
    if user_config.ui.ReverseSearchSorting.get_bool():
        enumerated_packages = list(reversed(enumerated_packages))

    for pkg_idx, package in enumerated_packages:
        # @TODO: return only packages for the current architecture
        idx = ""
        if enumerated:
            idx = bold_line(f"{pkg_idx+1}) ")

        pkg_name = package.name
        if args.quiet:
            print_stdout(f"{idx}{pkg_name}")
        else:

            repo = color_line("aur/", ColorsHighlight.red)
            if isinstance(package, pyalpm.Package):
                repo = pretty_format_repo_name(package.db.name)

            groups = ""
            if getattr(package, "groups", None):
                groups = color_line("({}) ".format(  # pylint: disable=consider-using-f-string
                    " ".join(package.groups),
                ), GROUP_COLOR)

            installed = ""
            if pkg_name in local_pkgs_names:
                installed = color_line(
                    translate("[installed: {version}]").format(
                        version=local_pkgs_versions[pkg_name],
                    ) + " "
                    if package.version != local_pkgs_versions[pkg_name] else
                    translate("[installed]") + " ",
                    ColorsHighlight.cyan,
                )

            rating = ""
            if (
                    isinstance(package, AURPackageInfo)
            ) and (
                package.numvotes is not None
            ) and (
                package.popularity is not None
            ):
                rating = color_line(
                    f"({package.numvotes}, {package.popularity:.2f})",
                    Colors.yellow,
                )

            color_config = user_config.colors
            version_color = color_config.Version.get_int()
            version = package.version

            if isinstance(package, AURPackageInfo) and package.outofdate is not None:
                version_color = color_config.VersionDiffOld.get_int()
                version = "{} [{}: {}]".format(  # pylint: disable=consider-using-f-string
                    package.version,
                    translate("outofdate"),
                    datetime.fromtimestamp(
                        package.outofdate,
                        tz=DEFAULT_TIMEZONE,
                    ).strftime("%Y/%m/%d"),
                )

            last_updated = ""
            if user_config.ui.DisplayLastUpdated.get_bool():
                last_update_date = None

                if isinstance(package, pyalpm.Package):
                    last_update_date = package.builddate
                if isinstance(package, AURPackageInfo):
                    last_update_date = package.lastmodified

                last_updated = color_line(
                    " (last updated: {})".format(  # pylint: disable=consider-using-f-string
                        datetime.fromtimestamp(
                            last_update_date,
                            tz=DEFAULT_TIMEZONE,
                        ).strftime("%Y/%m/%d")
                        if last_update_date is not None
                        else "unknown",
                    ),
                    ColorsHighlight.black,
                )

            print_stdout("{}{}{} {} {}{}{}{}".format(  # pylint: disable=consider-using-f-string
                idx,
                repo,
                bold_line(pkg_name),
                color_line(version, version_color),
                groups,
                installed,
                rating,
                last_updated,
            ))
            print_stdout(format_paragraph(f"{package.desc}"))
    return sorted_packages
