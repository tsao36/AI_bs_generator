"""
A CLI parser for interacting with ArtifactoryAPI

Exit Codes:
    40: No Matching Artifact (for --sha1)
"""

import sys
import argparse
import Sherlock
import ArtifactoryAPI

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--sha1", type=str, required=False, help="Firmware SHA1 to get Artifact for")
    parser.add_argument("-a", "--artifact", type=str, required=False, help="Artifact Path to get SHA1 for")
    parser.add_argument("-v", "--valid-only", action="store_true", help="Only return valid artifacts")
    parser.add_argument("-o", "--output", type=str, required=False, help="Save output to file as well as STDOUT.")
    parser.add_argument(
        "-u", "--upload", nargs=2, metavar=("file_to_upload", "target_path"), required=False, help="Upload an artifact."
    )
    parser.add_argument("-rmdir", "--remove_dir", type=str, help="Remove an (empty) directory from Artifactory")
    parser.add_argument("-mkdir", "--make_dir", type=str, help="Creates an (empty) directory in Artifactory")
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        default=False,
        help="Removing a non-empty directory (with its contents) is possible.",
    )
    parser.add_argument("-d", "--debug", action="store_true", default=False, help="Verbose logging")
    args = parser.parse_args()

    artifactory = ArtifactoryAPI.Artifactory(Sherlock.Artifactory.server)

    if args.sha1:
        try:
            additional_params = additional_params = (
                ArtifactoryAPI.VALID_ARTIFACT_PROPS_REMOTE if args.valid_only else None
            )
            result = artifactory.path_from_sha1(args.sha1, additional_params)
            print(result)
            if filepath := args.output:
                with open(filepath, mode="w", encoding="utf-8") as out_file:
                    out_file.write(result)
        except ArtifactoryAPI.NoMatchingArtifact as ex:
            print(ex)
            sys.exit(40)

    elif args.artifact:
        result = artifactory.sha1_from_path(args.artifact)
        print(result)
        if filepath := args.output:
            with open(filepath, mode="w", encoding="utf-8") as out_file:
                out_file.write(result)

    if args.upload:
        artifactory.deploy_artifact(args.upload[0], args.upload[1])
    if args.remove_dir:
        artifactory.delete_folder(args.remove_dir, args.recursive)
    if args.make_dir:
        artifactory.create_folder(args.make_dir)
